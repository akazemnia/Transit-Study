import streamlit as st
import pandas as pd
import folium
import geopandas as gpd
import os
import requests
import zipfile
import io
import shutil
from folium import FeatureGroup, LayerControl, CircleMarker, PolyLine
from streamlit_folium import st_folium
from shapely.geometry import shape
from shapely.ops import unary_union
import branca.colormap as cm

st.set_page_config(layout="wide", page_title="Maryland Transit & Population Visualizer")

# Maryland MTA GTFS feeds
GTFS_FEEDS = {
    "Local Bus": {
        "url": "https://feeds.mta.maryland.gov/gtfs/local-bus",
        "folder": "bus",
        "color": "blue"
    },
    "Light Rail": {
        "url": "https://feeds.mta.maryland.gov/gtfs/light-rail",
        "folder": "light_rail",
        "color": "purple"
    },
    "Metro Subway": {
        "url": "https://feeds.mta.maryland.gov/gtfs/metro",
        "folder": "metro",
        "color": "red"
    },
    "MARC Train": {
        "url": "https://feeds.mta.maryland.gov/gtfs/marc",
        "folder": "marc_train",
        "color": "green"
    },
    "Commuter Bus": {
        "url": "https://feeds.mta.maryland.gov/gtfs/commuter-bus",
        "folder": "commuter_bus",
        "color": "orange"
    }
}

st.title("🚇 Maryland Transit & Population Visualizer")

# Downloader function
def download_gtfs(label, url, folder):
    st.write(f"📅 Downloading {label} GTFS data...")
    try:
        r = requests.get(url)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        if os.path.exists(folder):
            shutil.rmtree(folder)
        os.makedirs(folder)
        z.extractall(folder)
        st.success(f"{label} GTFS data downloaded and extracted.")
    except Exception as e:
        st.error(f"Failed to download {label} GTFS: {e}")

# Sidebar controls
with st.sidebar:
    st.subheader("Options")
    if st.button("🔄 Download/Refresh All GTFS Data"):
        for label, cfg in GTFS_FEEDS.items():
            download_gtfs(label, cfg["url"], cfg["folder"])

    selected_modes = st.multiselect("Select transit modes to display", list(GTFS_FEEDS.keys()), default=list(GTFS_FEEDS.keys()))
    show_stops = st.checkbox("Show stops", value=True)
    show_population = st.checkbox("Show population density layer", value=True)

# --- Fetch population data directly from Census API ---
@st.cache_data
def fetch_population_data():
    counties = [
        "001", "003", "005", "009", "011", "013", "015", "017", "019", "021", "023", "025", "027",
        "029", "031", "033", "035", "037", "039", "041", "043", "045", "047", "510"  # Baltimore City (510) is unique
    ]
    url_base = "https://api.census.gov/data/2023/acs/acs5"
    all_data = []

    for county in counties:
        params = {
            "get": "NAME,B01003_001E",
            "for": "block group:*",
            "in": f"state:24 county:{county}"
        }
        try:
            r = requests.get(url_base, params=params, verify=False)
            r.raise_for_status()
            df = pd.DataFrame(r.json()[1:], columns=["NAME", "population", "state", "county", "tract", "block_group"])
            df["GEOID"] = df["state"] + df["county"] + df["tract"] + df["block_group"]
            df["population"] = pd.to_numeric(df["population"], errors="coerce")
            all_data.append(df)
        except Exception as e:
            st.warning(f"Failed to fetch data for county {county}: {e}")

    if all_data:
        full_df = pd.concat(all_data, ignore_index=True)
        return full_df
    else:
        return None

population_df = fetch_population_data()

# Load Block Group geometries
@st.cache_data
def load_bg_shapes():
    tiger_url = "https://www2.census.gov/geo/tiger/TIGER2024/BG/tl_2024_24_bg.zip"
    try:
        r = requests.get(tiger_url, verify=False)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            zf.extractall("blockgroups")
        gdf = gpd.read_file("blockgroups/tl_2024_24_bg.shp")
        return gdf.to_crs(epsg=4326)
    except Exception as e:
        st.error(f"Failed to load block group shapefile: {e}")
        return None

shapes_gdf = load_bg_shapes()

# Merge geometry with population
if population_df is not None and shapes_gdf is not None:
    shapes_gdf["GEOID"] = shapes_gdf["GEOID"].astype(str)
    merged = shapes_gdf.merge(population_df[["GEOID", "population"]], on="GEOID", how="left")
    merged = merged[merged["population"].notna() & (merged["population"] > 0)]
    merged["area_km2"] = merged.geometry.to_crs(epsg=3857).area / 1e6
    merged["pop_density"] = merged["population"] / merged["area_km2"]  
    merged["pop_density"] = merged["pop_density"].clip(upper = 14000)
else:
    merged = None

# Create folium map
m = folium.Map(location=[39.3, -76.6], zoom_start=10, tiles="cartodbpositron")

max_pop_density = int(merged["pop_density"].max() // 1000 + 1) * 1000
bins = list(range(0, max_pop_density + 1000, max_pop_density // 10))
colormap = cm.linear.YlOrRd_09.scale(0, max_pop_density)
colormap = cm.linear.YlOrRd_09.scale(0, max_pop_density).to_step(n=len(bins))


# Add choropleth layer with explicit threshold scale
# Add choropleth layer with fill_color as string (color brewer name) and threshold_scale for bins
if show_population and merged is not None:
    folium.Choropleth(
        geo_data=merged,
        name="Population Density",
        data=merged,
        columns=["GEOID", "pop_density"],
        key_on="feature.properties.GEOID",
        fill_color="YlOrRd",
        threshold_scale=bins,
        fill_opacity=0.7,
        line_opacity=0.2,
        legend_name="Population Density (people/km²)",
        highlight=True,
        smooth_factor=0,
    ).add_to(m)
    # Add the separate colormap control to map
    #colormap.caption = 'Population Density (people per km²)'
    #colormap.add_to(m)

# Function to draw GTFS routes/stops
def plot_gtfs(folder, color, label):
    try:
        shapes = pd.read_csv(os.path.join(folder, "shapes.txt"))
        trips = pd.read_csv(os.path.join(folder, "trips.txt"))
        routes = pd.read_csv(os.path.join(folder, "routes.txt"))
        stops = pd.read_csv(os.path.join(folder, "stops.txt"))

        trip_routes = pd.merge(
            trips[['route_id', 'shape_id']],
            routes[['route_id', 'route_short_name']],
            on='route_id', how='left'
        )

        route_group = FeatureGroup(name=f"{label} Routes", show=True)
        stop_group = FeatureGroup(name=f"{label} Stops", show=show_stops)

        plotted_shapes = set()
        for _, row in trip_routes.iterrows():
            shape_id = row['shape_id']
            if shape_id in plotted_shapes:
                continue
            plotted_shapes.add(shape_id)

            shape_pts = shapes[shapes['shape_id'] == shape_id].sort_values('shape_pt_sequence')
            coords = shape_pts[['shape_pt_lat', 'shape_pt_lon']].values.tolist()

            PolyLine(
                locations=coords,
                color=color,
                weight=2,
                opacity=0.7,
                tooltip=f"{label} Route {row['route_short_name']}"
            ).add_to(route_group)

        if show_stops:
            for _, stop in stops.iterrows():
                CircleMarker(
                    location=[stop['stop_lat'], stop['stop_lon']],
                    radius=2,
                    color=color,
                    fill=True,
                    fill_opacity=0.6,
                    popup=f"{label} Stop: {stop['stop_name']}"
                ).add_to(stop_group)

        route_group.add_to(m)
        if show_stops:
            stop_group.add_to(m)

    except Exception as e:
        st.error(f"Failed to load {label} data: {e}")

# Plot all selected modes
for mode in selected_modes:
    cfg = GTFS_FEEDS[mode]
    plot_gtfs(cfg['folder'], cfg['color'], mode)

LayerControl(collapsed=False).add_to(m)
st_data = st_folium(m, width=1300, height=700)
