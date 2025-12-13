# Cesium Migration Guide

This document describes the migration from Three.js to Cesium for the 3dSAGER demo application.

## Changes Made

### 1. Replaced Three.js with Cesium
- **Removed**: Local Three.js files (`three.min.js`, `OrbitControls.js`)
- **Added**: Cesium CDN integration
- **New File**: `static/js/cesium-cityjson-viewer.js` - Cesium-based CityJSON viewer

### 2. New Features
- **Clickable Buildings**: Buildings can now be clicked to view their matches
- **Location Map**: Added Leaflet map showing The Hague location
- **Matches Window**: Modal window displaying building matches with confidence scores
- **Better Camera Controls**: Geospatial-aware camera with smooth navigation
- **Improved Visualization**: Better rendering, lighting, and shadows

### 3. Updated Files
- `templates/demo.html` - Updated to use Cesium CDN, added location map and matches window
- `static/js/demo.js` - Added location map initialization and matches window functions
- `static/css/demo.css` - Added styles for matches window and location map
- `app.py` - Added `/api/building/matches/<building_id>` endpoint

## Setup Instructions

### 1. Get Cesium Ion Access Token
1. Go to https://cesium.com/ion/
2. Sign up for a free account
3. Copy your access token
4. Open `templates/demo.html`
5. Replace `YOUR_CESIUM_ION_ACCESS_TOKEN_HERE` with your token

```html
<script>
    Cesium.Ion.defaultAccessToken = 'YOUR_TOKEN_HERE';
</script>
```

### 2. Coordinate System Configuration
The CityJSON viewer assumes coordinates are in a local system relative to The Hague. If your CityJSON uses a different coordinate reference system (CRS), you may need to adjust the coordinate conversion in `cesium-cityjson-viewer.js`:

```javascript
this.defaultLocation = {
    longitude: 4.3007,  // The Hague longitude (WGS84)
    latitude: 52.0705,  // The Hague latitude (WGS84)
    originX: 0,         // Adjust based on your CRS origin
    originY: 0,         // Adjust based on your CRS origin
    metersPerDegree: 111320.0
};
```

For proper CRS transformation (e.g., RD New EPSG:28992), consider using a library like `proj4js`.

### 3. Building Matches API
The `/api/building/matches/<building_id>` endpoint currently returns mock data. To use real matching results:

1. Load your matching results from the saved model files
2. Update the `get_building_matches()` function in `app.py`
3. Return actual matches based on your matching algorithm results

## Usage

### Viewing Buildings
1. Select a CityJSON file from Source A or Source B
2. The 3D model loads in the Cesium viewer
3. Camera automatically fits to the model

### Clicking Buildings
1. Click on any building in the 3D viewer
2. The building is highlighted (yellow)
3. A matches window opens showing potential matches
4. Click "View in 3D" on a match to view it (feature can be extended)

### Location Map
- The sidebar shows a Leaflet map with The Hague location
- Marker indicates the demo location

## Key Differences from Three.js

| Feature | Three.js | Cesium |
|---------|----------|--------|
| Coordinate System | Local 3D | Geodetic (WGS84) |
| Camera | Manual controls | Geospatial-aware |
| Click Detection | Manual raycasting | Built-in picking |
| Map Integration | None | Built-in globe |
| Terrain | None | Built-in support |
| Performance | Good | Excellent for large datasets |

## Troubleshooting

### Buildings Not Appearing
- Check browser console for errors
- Verify CityJSON file format
- Check coordinate conversion settings
- Ensure Cesium token is set

### Buildings in Wrong Location
- Adjust `defaultLocation` in `cesium-cityjson-viewer.js`
- Check your CityJSON CRS metadata
- Consider using proper CRS transformation

### Matches Not Showing
- Check browser console for API errors
- Verify `/api/building/matches/<id>` endpoint is working
- Check network tab for failed requests

## Next Steps

1. **Get Cesium Token**: Essential for full functionality
2. **Configure Coordinates**: Adjust for your CRS if needed
3. **Connect Real Matches**: Update API to use actual matching results
4. **Extend Features**: Add building highlighting, match visualization, etc.

## Files Changed
- `templates/demo.html`
- `static/js/cesium-cityjson-viewer.js` (new)
- `static/js/demo.js`
- `static/css/demo.css`
- `app.py`

## Files No Longer Used
- `static/js/cityjson-viewer.js` (Three.js version)
- `static/js/cityjson-parser.js` (can be kept for reference)
- `static/js/threejs/*` (Three.js libraries)

