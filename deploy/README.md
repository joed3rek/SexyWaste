# Deploy data

Prebuilt OpenStreetMap cache for the HSR Layout pilot, copied into `data/cache/` by the
Render build (`render.yaml`). Hosted servers cannot rely on Nominatim and Overpass, which
often block or stall requests from cloud IP addresses.

- `cache/pilots/hsr/`: sectors, buildings, land use, parks and POIs (GeoJSON)
- `cache/hsr.graphml`: drivable road network

Data © OpenStreetMap contributors, ODbL. To refresh it, delete the files under `data/cache/`,
run the app locally once so it downloads them again, then copy them here.
