// Cesium CityJSON Viewer with Clickable Buildings
// Converts CityJSON to Cesium entities with geospatial positioning

// Color map constant - don't recreate on every call
const BUILDING_COLOR_MAP = {
    'blue': Cesium.Color.fromBytes(116, 151, 223, 255),      // Default blue
    'orange': Cesium.Color.fromBytes(255, 152, 0, 255),      // Orange - has features
    'yellow': Cesium.Color.fromBytes(255, 235, 59, 255),     // Yellow - has BKAFI pairs
    'green': Cesium.Color.fromBytes(76, 175, 80, 255),       // Green - true match
    'red': Cesium.Color.fromBytes(244, 67, 54, 255),         // Red - false positive
    'darkgray': Cesium.Color.fromBytes(97, 97, 97, 255),     // Dark gray - no match
    // Step 4 sub-stage colors:
    'cand_misaligned': Cesium.Color.fromBytes(220, 110,  60, 255),   // 4a — misaligned cand
    'anchor_cand':     Cesium.Color.fromBytes(170,  90, 230, 255),   // 4b — anchor (cand side)
    'anchor_index':    Cesium.Color.fromBytes(  0, 200, 230, 255),   // 4b — anchor (index side)
    'false_negative':  Cesium.Color.fromBytes(120,  80, 140, 255)    // 4d — false negative
};

// Non-selected layer: semi-transparent fill + strong contour so overlapping buildings are distinguishable
const INACTIVE_LAYER_FILL = Cesium.Color.fromBytes(180, 180, 180, 55);
const INACTIVE_LAYER_OUTLINE = Cesium.Color.fromBytes(80, 80, 80, 220);
const SELECTED_LAYER_OUTLINE = Cesium.Color.BLACK.withAlpha(0.5);

// Pre-computed source material palettes — created once, never re-allocated per entity.
// getMaterialForObjectType returns from these instead of calling fromBytes() each time.
const _MAT = {
    A: {
        Building:             Cesium.Color.fromBytes(116, 151, 223, 255),
        BuildingPart:         Cesium.Color.fromBytes(116, 151, 223, 255),
        BuildingInstallation: Cesium.Color.fromBytes(116, 151, 223, 255),
        Bridge:               Cesium.Color.fromBytes(153, 153, 153, 255),
        BridgePart:           Cesium.Color.fromBytes(153, 153, 153, 255),
        Road:                 Cesium.Color.fromBytes(153, 153, 153, 255),
        WaterBody:            Cesium.Color.fromBytes( 77, 166, 255, 255),
        PlantCover:           Cesium.Color.fromBytes( 57, 172,  57, 255),
        LandUse:              Cesium.Color.fromBytes(255, 255, 179, 255),
        _fallback:            Cesium.Color.fromBytes(136, 136, 136, 255),
    },
    B: {
        Building:             Cesium.Color.fromBytes( 38, 166, 154, 255),
        BuildingPart:         Cesium.Color.fromBytes( 38, 166, 154, 255),
        BuildingInstallation: Cesium.Color.fromBytes( 38, 166, 154, 255),
        Bridge:               Cesium.Color.fromBytes( 70, 130, 120, 255),
        BridgePart:           Cesium.Color.fromBytes( 70, 130, 120, 255),
        Road:                 Cesium.Color.fromBytes(153, 153, 153, 255),
        WaterBody:            Cesium.Color.fromBytes( 77, 166, 255, 255),
        PlantCover:           Cesium.Color.fromBytes( 57, 172,  57, 255),
        LandUse:              Cesium.Color.fromBytes(255, 255, 179, 255),
        _fallback:            Cesium.Color.fromBytes( 38, 166, 154, 255),
    },
};

class CesiumCityJSONViewer {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        this.viewer = null;
        this.cityObjects = {};
        this.cityObjectsByFile = new Map();
        this.buildingEntities = new Map(); // Store building entities for click handling
        this.idMapping = new Map(); // Pre-computed ID mapping for fast lookups: numericId -> [all variations]
        this.isInitialized = false;
        this.isLoading = false;
        this.pendingLoads = [];
        this.layerEntities = new Map();
        this.layerSource = new Map(); // filePath -> source ('A' or 'B')
        this.currentLayerFilePath = null;
        this.currentLayerSource = null;
        this.boundingBox = null; // Store bounding box for camera fitting
        this.crs = null; // Store coordinate reference system from metadata
        this.sourceCRS = null; // Source CRS from CityJSON metadata
        this.isComparisonViewer = options.isComparisonViewer || false; // Lightweight mode for comparison
        this.initialCameraPosition = null; // Store initial camera position after first file load
        
        // The Hague coordinates (default location)
        this.defaultLocation = {
            longitude: 4.3007,  // The Hague longitude (WGS84)
            latitude: 52.0705,   // The Hague latitude (WGS84)
            height: 5000,       // Initial camera height
        };
        
        this.init();
    }
    
    ensureProj4Defs() {
        if (typeof proj4 === 'undefined' || !proj4.defs) {
            return;
        }
        // RD New (EPSG:28992) used for The Hague data. EPSG:7415 is a compound CRS
        // with the same horizontal component; treat it as 28992 for x/y transforms.
        if (!proj4.defs['EPSG:28992']) {
            proj4.defs('EPSG:28992',
                '+proj=sterea +lat_0=52.15616055555555 +lon_0=5.38763888888889 ' +
                '+k=0.9999079 +x_0=155000 +y_0=463000 +ellps=bessel ' +
                '+towgs84=565.417,50.3319,465.552,-0.398957,0.343988,-1.8774,4.0725 ' +
                '+units=m +no_defs'
            );
        }
    }

    async init() {
        // Check if container exists
        if (!this.container) {
            console.error('Viewer container not found');
            return;
        }
        
        // Check if Cesium is loaded
        if (typeof Cesium === 'undefined') {
            console.error('Cesium library not loaded. Please include Cesium CDN.');
            this.showError('Cesium library not loaded. Please check your internet connection and refresh the page.');
            return;
        }
        
        // No token needed - using ellipsoid terrain (flat surface) instead of world terrain
        // This is perfect for displaying CityJSON building models which already include their geometry
        
        try {
            // Initialize Cesium Viewer with ellipsoid terrain (no token required)
            // Using OpenStreetMap imagery provider to avoid Ion token requirement
            const ionToken = (typeof window !== 'undefined' && window.CESIUM_ION_TOKEN) ? window.CESIUM_ION_TOKEN : '';
            if (ionToken) {
                Cesium.Ion.defaultAccessToken = ionToken;
            }

            const osmImagery = new Cesium.OpenStreetMapImageryProvider({
                url: 'https://a.tile.openstreetmap.org/'
            });
            this.ensureProj4Defs();
            let imageryProvider = osmImagery;
            let ionImageryProvider = null;
            if (ionToken) {
                try {
                    ionImageryProvider = await Cesium.IonImageryProvider.fromAssetId(2);
                    imageryProvider = ionImageryProvider;
                } catch (error) {
                    console.warn('Failed to load Cesium Ion imagery, falling back to OSM.', error);
                    imageryProvider = osmImagery;
                }
            }
            this.osmImageryProvider = osmImagery;
            this.ionImageryProvider = ionImageryProvider;
            
            // For comparison viewers, use minimal features for faster loading
            const viewerOptions = this.isComparisonViewer ? {
                terrainProvider: new Cesium.EllipsoidTerrainProvider(),
                imageryProvider: false, // No imagery for faster loading
                baseLayerPicker: false,
                vrButton: false,
                geocoder: true,
                homeButton: false,
                sceneModePicker: true,
                navigationHelpButton: true,
                animation: false,
                timeline: false,
                fullscreenButton: true,
                infoBox: false,
                selectionIndicator: false,
                shouldAnimate: false
            } : {
                terrainProvider: new Cesium.EllipsoidTerrainProvider(), // Simple ellipsoid terrain
                imageryProvider,
                baseLayerPicker: true,
                vrButton: true,
                geocoder: true,
                homeButton: true,
                sceneModePicker: true,
                navigationHelpButton: true,
                animation: false,
                timeline: false,
                fullscreenButton: true,
                infoBox: false, // Disable Cesium info box - using custom window instead
                selectionIndicator: true // Show selection indicator
            };
            
            this.viewer = new Cesium.Viewer(this.container, viewerOptions);

            if (!this.isComparisonViewer) {
                // Ensure an imagery layer is present (some providers may fail silently)
                this.viewer.imageryLayers.removeAll();
                try {
                    this.viewer.imageryLayers.addImageryProvider(imageryProvider);
                } catch (error) {
                    console.warn('Failed to apply imagery provider, falling back to OSM.', error);
                    this.viewer.imageryLayers.addImageryProvider(osmImagery);
                }

                if (ionImageryProvider && ionImageryProvider.errorEvent) {
                    let fallbackApplied = false;
                    ionImageryProvider.errorEvent.addEventListener((error) => {
                        if (fallbackApplied) {
                            return;
                        }
                        fallbackApplied = true;
                        console.warn('Cesium Ion imagery failed, falling back to OSM.', error);
                        this.viewer.imageryLayers.removeAll();
                        this.viewer.imageryLayers.addImageryProvider(osmImagery);
                        if (this.viewer.scene && this.viewer.scene.requestRender) {
                            this.viewer.scene.requestRender();
                        }
                    });
                }
            }
            
            // Set background colors (keep neutral so imagery stands out)
            this.viewer.scene.backgroundColor = Cesium.Color.fromCssColorString('#0f172a');
            this.viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#1e293b');
            this.viewer.scene.globe.enableLighting = false;
            
            // Set initial camera position (The Hague, Netherlands) - Top-down view
            this.viewer.camera.setView({
                destination: Cesium.Cartesian3.fromDegrees(
                    this.defaultLocation.longitude,
                    this.defaultLocation.latitude,
                    this.defaultLocation.height
                ),
                orientation: {
                    heading: Cesium.Math.toRadians(0),
                    pitch: Cesium.Math.toRadians(-90), // -90 degrees = looking straight down (top-down view)
                    roll: 0.0
                }
            });
            
            // Setup click handler for buildings (only for main viewer, not comparison)
            if (!this.isComparisonViewer) {
                this.setupClickHandler();
            }
            
            // Remove placeholder if exists
            this.clearPlaceholder();
            
            // Add touch-friendly map control buttons (zoom, rotate, tilt, home) for non-comparison viewer
            if (!this.isComparisonViewer && this.container) {
                this._injectMapControls();
            }
            
            // For comparison viewers, disable globe rendering for better performance
            if (this.isComparisonViewer) {
                this.viewer.scene.globe.show = false;
                this.viewer.scene.skyBox.show = false;
                this.viewer.scene.sun.show = false;
                this.viewer.scene.moon.show = false;
            }
            
            this.isInitialized = true;
            console.log(`Cesium CityJSON Viewer initialized successfully (${this.isComparisonViewer ? 'comparison mode' : 'full mode'})`);
        } catch (error) {
            console.error('Error initializing Cesium viewer:', error);
            this.showError('Failed to initialize 3D viewer: ' + error.message);
            throw error; // Re-throw so calling code knows initialization failed
        }
    }
    
    setupClickHandler() {
        const handler = new Cesium.ScreenSpaceEventHandler(this.viewer.scene.canvas);
        
        // Handle left click
        handler.setInputAction((click) => {
            const pickedObject = this.viewer.scene.pick(click.position);
            
            if (Cesium.defined(pickedObject) && pickedObject.id) {
                const entity = pickedObject.id;

                // ── Marker label clicked — fly to that building ───────────────
                if (entity.markerBuildingId && entity.markerPosition) {
                    this.viewer.camera.flyTo({
                        destination: entity.markerPosition,
                        orientation: {
                            heading: this.viewer.camera.heading,
                            pitch:   Cesium.Math.toRadians(-45),
                            roll:    0,
                        },
                        offset: new Cesium.HeadingPitchRange(
                            this.viewer.camera.heading,
                            Cesium.Math.toRadians(-45),
                            180
                        ),
                        duration: 1.4,
                        easingFunction: Cesium.EasingFunction.QUARTIC_IN_OUT,
                    });
                    return;
                }

                // Building was clicked
                const buildingId = entity.buildingId;
                // Find all entities at this building (same ID from different layers/sources)
                const allEntities = this._findEntitiesById(buildingId) || [];
                const sources = [];
                const seenPaths = new Set();
                allEntities.forEach((e) => {
                    if (!e.filePath || seenPaths.has(e.filePath)) return;
                    seenPaths.add(e.filePath);
                    const src = this.layerSource.get(e.filePath) || 'A';
                    const shortName = e.filePath.split('/').pop() || e.filePath;
                    sources.push({ filePath: e.filePath, source: src, name: shortName });
                });
                const cityObjects = entity.filePath
                    ? (this.cityObjectsByFile.get(entity.filePath) || {})
                    : this.cityObjects;
                if (buildingId && cityObjects[buildingId]) {
                    if (entity.filePath && typeof window.setActiveFileFromViewer === 'function') {
                        window.setActiveFileFromViewer(entity.filePath);
                    }
                    this.onBuildingClicked(buildingId, entity, cityObjects[buildingId], { sources });
                }
            } else {
                // Clicked on nothing - close info box
                this.viewer.selectedEntity = undefined;
            }
        }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
        
        // Handle mouse move for highlighting
        handler.setInputAction((movement) => {
            const pickedObject = this.viewer.scene.pick(movement.endPosition);
            
            if (Cesium.defined(pickedObject) && pickedObject.id) {
                // Show a pointer for both buildings and clickable markers
                this.viewer.canvas.style.cursor = 'pointer';
            } else {
                this.viewer.canvas.style.cursor = 'default';
            }
        }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
    }
    
    onBuildingClicked(buildingId, entity, cityObjectOverride, options = {}) {
        const cityObject = cityObjectOverride || this.cityObjects[buildingId];
        
        // Select the entity (for highlighting)
        this.viewer.selectedEntity = entity;
        
        // Store the current material as the "original" (this will be the correct color after updates)
        if (!entity.originalMaterial) {
            entity.originalMaterial = entity.polygon.material;
        }
        
        // Highlight the building temporarily
        entity.polygon.material = Cesium.Color.YELLOW.withAlpha(0.7);
        
        // Reset highlight after 2 seconds
        setTimeout(() => {
            if (entity.polygon) {
                entity.polygon.material = entity.originalMaterial || entity.polygon.material;
            }
        }, 2000);
        
        // Show custom building properties window; pass sources when building exists in multiple layers
        if (window.showBuildingProperties) {
            window.showBuildingProperties(buildingId, cityObject, options);
        }
    }
    
    loadBuildingMatches(buildingId, cityObject) {
        // Call your API to get matches
        fetch(`/api/building/matches/${buildingId}`)
            .then(response => response.json())
            .then(data => {
                // Show matches in a window
                if (window.showBuildingMatches) {
                    window.showBuildingMatches(
                        buildingId,
                        cityObject.attributes?.name || buildingId,
                        data.matches || []
                    );
                }
            })
            .catch(error => {
                console.error('Error loading matches:', error);
                // Show matches window even if API fails (for demo)
                if (window.showBuildingMatches) {
                    window.showBuildingMatches(
                        buildingId,
                        cityObject.attributes?.name || buildingId,
                        []
                    );
                }
            });
    }
    
    loadCityJSON(filePath, options = {}) {
        if (!this.isInitialized) {
            console.error('Viewer not initialized');
            return;
        }

        console.log('Loading CityJSON file:', filePath);
        // `url`  — override the API endpoint (used by Step 4 to fetch from
        //          /api/alignment/cityjson?stage=…). Defaults to /api/data/file.
        const { append = false, source = null, url = null } = options;

        if (this.isLoading) {
            this.pendingLoads.push({ filePath, options });
            this.updateLoadingProgress('Queueing layer load...');
            return;
        }

        this.isLoading = true;
        this.currentLayerFilePath = filePath;
        this.currentLayerSource = source;

        // Clear existing buildings
        if (!append) {
            this.clearBuildings();
        }

        // Show loading
        this.showLoading();

        // Fetch CityJSON
        const apiUrl = url || `/api/data/file?path=${encodeURIComponent(filePath)}`;
        const fetchStartTime = performance.now();
        
        this.updateLoadingProgress('Downloading file...');
        
        fetch(apiUrl)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                const fetchTime = performance.now() - fetchStartTime;
                console.log(`File download took: ${(fetchTime / 1000).toFixed(2)}s`);
                this.updateLoadingProgress('Parsing JSON data...');
                return response.json();
            })
            .then(data => {
                const parseTime = performance.now() - fetchStartTime;
                console.log(`JSON parsing took: ${(parseTime / 1000).toFixed(2)}s`);
                console.log(`CityJSON data size: ${JSON.stringify(data).length} characters`);
                this.parseCityJSON(data);
            })
            .catch(error => {
                console.error('Error loading CityJSON:', error);
                this.hideLoading();
                this.showError('Failed to load CityJSON: ' + error.message);
            });
    }
    
    /**
     * Fast loader for pre-baked CityJSON files (*.prebaked.json).
     * Positions are already WGS84; no proj4, no transform math needed.
     * @private
     */
    _parsePrebaked(data) {
        const t0 = performance.now();
        const buildings = data.buildings || {};
        const ids = Object.keys(buildings);
        const total = ids.length;

        if (total === 0) {
            this.hideLoading();
            return;
        }

        // Build a CityObjects-compatible lookup so the click handler can find
        // building data by ID (same structure the original parseCityJSON creates).
        const cityObjectsMap = {};
        for (const id of ids) {
            const b = buildings[id];
            cityObjectsMap[id] = { type: b.type || 'Building', attributes: b.attributes || {} };
        }
        this.cityObjects = cityObjectsMap;
        if (this.currentLayerFilePath) {
            this.cityObjectsByFile.set(this.currentLayerFilePath, cityObjectsMap);
        }

        // Compute geographic bounds while creating entities so fitCameraToBuildings works.
        let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;

        // Track whether we need to init the geographic bounds object.
        if (!this.geographicBounds) {
            this.geographicBounds = {
                minLat: Infinity, maxLat: -Infinity,
                minLon: Infinity, maxLon: -Infinity,
            };
        }

        const createEntity = (id, bld) => {
            const positions = bld.positions; // [[lon,lat], ...]
            const height    = bld.height;

            if (!positions || positions.length < 3 || height <= 0) return;

            const cartesians = positions.map(([lon, lat]) => {
                if (lon < minLon) minLon = lon;
                if (lon > maxLon) maxLon = lon;
                if (lat < minLat) minLat = lat;
                if (lat > maxLat) maxLat = lat;
                // Altitude in the Cartesian3 is overridden by polygon.height below;
                // pass 0 to avoid any floating-point noise.
                return Cesium.Cartesian3.fromDegrees(lon, lat, 0);
            });

            const defaultColor = this.getMaterialForObjectType(
                bld.type || 'Building', this.currentLayerSource);

            const entity = this.viewer.entities.add({
                name: bld.attributes?.name || id,
                buildingId: id,
                filePath: this.currentLayerFilePath,
                cityObjectData: { type: bld.type, attributes: bld.attributes },
                defaultColor,  // cached for reliable restore in applyLayerVisualStyles
                polygon: {
                    hierarchy: cartesians,
                    extrudedHeight: height,
                    height: 0,
                    material: new Cesium.ColorMaterialProperty(defaultColor),
                    outline: true,
                    outlineColor: Cesium.Color.BLACK.withAlpha(0.3),
                    outlineWidth: 1,
                },
            });

            if (!this.buildingEntities.has(id)) {
                this.buildingEntities.set(id, []);
                this._updateIdMapping(id);
            }
            this.buildingEntities.get(id).push(entity);

            if (this.currentLayerFilePath) {
                if (!this.layerEntities.has(this.currentLayerFilePath)) {
                    this.layerEntities.set(this.currentLayerFilePath, []);
                }
                this.layerEntities.get(this.currentLayerFilePath).push(entity);
                if (this.currentLayerSource) {
                    this.layerSource.set(this.currentLayerFilePath, this.currentLayerSource);
                }
            }
        };

        // Batch processing — same pattern as parseCityJSON so the UI stays responsive.
        const batchSize = Math.min(500, Math.max(100, Math.floor(total / 10)));
        let entityCount = 0;
        let processed   = 0;

        this.updateLoadingProgress(`Processing ${total} buildings…`);

        const processBatch = (start) => {
            const end = Math.min(start + batchSize, total);
            for (let i = start; i < end; i++) {
                const id  = ids[i];
                const bld = buildings[id];
                const before = this.viewer.entities.values.length;
                createEntity(id, bld);
                if (this.viewer.entities.values.length > before) entityCount++;
            }
            processed = end;

            const pct = Math.round((processed / total) * 100);
            this.updateLoadingProgress(`Processing buildings… ${pct}% (${processed}/${total})`);

            if (end < total) {
                setTimeout(() => processBatch(end), 0);
            } else {
                // Merge computed bounds into the shared geographicBounds tracker.
                if (minLon !== Infinity) {
                    this.geographicBounds.minLon = Math.min(this.geographicBounds.minLon, minLon);
                    this.geographicBounds.maxLon = Math.max(this.geographicBounds.maxLon, maxLon);
                    this.geographicBounds.minLat = Math.min(this.geographicBounds.minLat, minLat);
                    this.geographicBounds.maxLat = Math.max(this.geographicBounds.maxLat, maxLat);
                }

                const elapsed = ((performance.now() - t0) / 1000).toFixed(2);
                console.log(`Pre-baked: loaded ${entityCount}/${total} entities in ${elapsed}s`);

                if (entityCount > 0 && !this.skipAutoFit) {
                    this.fitCameraToBuildings();
                }
                if (!this.isComparisonViewer) {
                    setTimeout(() => {
                        this.hideLoading();
                        const loadedFilePath = this.currentLayerFilePath;
                        const loadedSource = this.layerSource.get(loadedFilePath) || 'A';
                        if (typeof window.setActiveFileFromViewer === 'function' &&
                                loadedFilePath && loadedSource === 'A') {
                            window.setActiveFileFromViewer(loadedFilePath);
                        }
                    }, 500);
                }
            }
        };

        processBatch(0);
    }

    parseCityJSON(cityJSON) {
        // Fast path: pre-baked format has WGS84 footprints + heights pre-computed.
        // Skip all coordinate transforms, vertex loops, and proj4 calls.
        if (cityJSON.prebaked) {
            this._parsePrebaked(cityJSON);
            return;
        }

        const parseStartTime = performance.now();
        try {
            // Store city objects
            const cityObjects = cityJSON.CityObjects || {};
            this.cityObjects = cityObjects;
            if (this.currentLayerFilePath) {
                this.cityObjectsByFile.set(this.currentLayerFilePath, cityObjects);
            }
            const vertices = cityJSON.vertices || [];
            const transform = cityJSON.transform || null;
            
            // Pre-transform all vertices if transform is available (optimization)
            // This avoids repeated transform calculations per building
            let transformedVertices = null;
            if (transform && vertices.length > 0) {
                transformedVertices = vertices.map(vertex => [
                    vertex[0] * transform.scale[0] + transform.translate[0],
                    vertex[1] * transform.scale[1] + transform.translate[1],
                    vertex[2] * transform.scale[2] + transform.translate[2]
                ]);
            }
            
            console.log(`Parsing CityJSON: ${Object.keys(cityObjects).length} objects, ${vertices.length} vertices`);
            
            // Try to use metadata.geographicalExtent first (most accurate)
            // Format: [minx, miny, minz, maxx, maxy, maxz] in the file's CRS
            if (cityJSON.metadata && cityJSON.metadata.geographicalExtent) {
                const extent = cityJSON.metadata.geographicalExtent;
                if (extent.length >= 6) {
                    this.boundingBox = {
                        min: { x: extent[0], y: extent[1], z: extent[2] },
                        max: { x: extent[3], y: extent[4], z: extent[5] },
                        center: {
                            x: (extent[0] + extent[3]) / 2,
                            y: (extent[1] + extent[4]) / 2,
                            z: (extent[2] + extent[5]) / 2
                        }
                    };
                    console.log('Using geographicalExtent from metadata:', this.boundingBox);
                    // Store CRS info if available
                    this.crs = cityJSON.metadata.referenceSystem || null;
                    this.sourceCRS = this.crs;
                    if (this.crs) {
                        console.log('CityJSON CRS:', this.crs);
                    }
                }
            }
            
            // Store CRS from metadata even if no geographicalExtent
            if (!this.crs && cityJSON.metadata && cityJSON.metadata.referenceSystem) {
                this.crs = cityJSON.metadata.referenceSystem;
                this.sourceCRS = this.crs;
                console.log('CityJSON CRS from metadata:', this.crs);
            }
            
            // Fall back to calculating from vertices if no metadata
            if (!this.boundingBox) {
                this.calculateBoundingBox(vertices, transform);
                console.log('Calculated bounding box from vertices:', this.boundingBox);
            }
            
            // Process city objects in batches to avoid blocking UI
            const objectIds = Object.keys(cityObjects);
            const totalObjects = objectIds.length;
            let entityCount = 0;
            let processedCount = 0;
            
            // For comparison viewers with single building, render immediately (no batching needed)
            if (this.isComparisonViewer && totalObjects === 1) {
                console.log('Comparison viewer: rendering single building immediately');
                const objectId = objectIds[0];
                const cityObject = cityObjects[objectId];
                const geometries = cityObject.geometry || [];
                
                console.log(`Comparison viewer: Building ID: ${objectId}, geometries: ${geometries.length}, vertices: ${vertices.length}`);
                
                if (geometries.length === 0) {
                    console.error('Comparison viewer: No geometries found for building');
                    return;
                }
                
                geometries.forEach((geometry, geomIndex) => {
                    console.log(`Comparison viewer: Processing geometry ${geomIndex + 1}/${geometries.length}, type: ${geometry.type}`);
                    try {
                        const entity = this.createBuildingEntity(
                            objectId,
                            cityObject,
                            geometry,
                            transformedVertices || vertices,
                            null
                        );
                        if (entity) {
                            entityCount++;
                            console.log(`Comparison viewer: Successfully created entity ${entityCount} for ${objectId}`);
                        } else {
                            console.warn(`Comparison viewer: createBuildingEntity returned null for geometry ${geomIndex + 1}`);
                        }
                    } catch (entityError) {
                        console.error(`Comparison viewer: Error creating entity for geometry ${geomIndex + 1}:`, entityError);
                    }
                });
                
                const totalTime = performance.now() - parseStartTime;
                console.log(`Comparison viewer: Loaded ${totalObjects} city objects, ${entityCount} entities in ${(totalTime / 1000).toFixed(2)}s`);
                console.log(`Comparison viewer: Building entities map size: ${this.buildingEntities.size}`);
                console.log(`Comparison viewer: Building entities keys:`, Array.from(this.buildingEntities.keys()));
                
                if (entityCount === 0) {
                    console.error('Comparison viewer: WARNING - No entities were created!');
                }
                
                return; // Done - entities are created synchronously
            }
            
            // For regular viewers with multiple buildings, use batching
            // Increased batch size for better performance (200-300 is optimal for most cases)
            // Adjust based on file size: larger files = larger batches
            const batchSize = Math.min(300, Math.max(100, Math.floor(totalObjects / 20)));
            
            this.updateLoadingProgress(`Processing ${totalObjects} city objects...`);
            
            // Yield the main thread between batches so the browser can paint,
            // but resume immediately (next task queue slot, typically < 4 ms).
            // requestIdleCallback deliberately defers until idle time which adds
            // up to 100 ms of dead-wait per batch — avoid it here.
            const scheduleNextBatch = (callback) => setTimeout(callback, 0);
            
            const processBatch = (startIndex) => {
                const endIndex = Math.min(startIndex + batchSize, totalObjects);
                
                for (let i = startIndex; i < endIndex; i++) {
                    const objectId = objectIds[i];
                    const cityObject = cityObjects[objectId];
                    const geometries = cityObject.geometry || [];
                    
                    geometries.forEach(geometry => {
                        const entity = this.createBuildingEntity(
                            objectId,
                            cityObject,
                            geometry,
                            transformedVertices || vertices, // Use pre-transformed vertices if available
                            null // No transform needed if vertices are pre-transformed
                        );
                        if (entity) {
                            entityCount++;
                        }
                    });
                    processedCount++;
                }
                
                // Update progress
                const progress = Math.round((processedCount / totalObjects) * 100);
                this.updateLoadingProgress(`Processing buildings... ${progress}% (${processedCount}/${totalObjects})`);
                
                // Continue with next batch or finish
                if (endIndex < totalObjects) {
                    // Use requestIdleCallback for better scheduling
                    scheduleNextBatch(() => processBatch(endIndex));
                } else {
                    // All done
                    const totalTime = performance.now() - parseStartTime;
                    console.log(`Loaded ${totalObjects} city objects, ${entityCount} entities in ${(totalTime / 1000).toFixed(2)}s`);
                    console.log(`Building entities map size: ${this.buildingEntities.size}`);
                    
                    // For comparison viewers, don't show loading/hide it immediately
                    if (!this.isComparisonViewer) {
                        this.updateLoadingProgress('Finalizing...');
                    }
                    
                    // Fit camera to all buildings (unless auto-fit is disabled for comparison viewers)
                    if (entityCount > 0 && !this.skipAutoFit) {
                        this.fitCameraToBuildings();
                        // Initial camera position will be stored in fitCameraToBuildings after animation completes
                    }
                    
                    // Small delay before hiding loading to show completion (only for main viewer)
                    if (!this.isComparisonViewer) {
                        setTimeout(() => {
                            this.hideLoading();
                            // Notify demo.js that all entities are ready so it can re-apply the
                            // current pipeline color scheme (handles the race where pipeline steps
                            // complete before batch entity creation finishes).
                            // Only do this for Source A files to avoid overriding selectedFile
                            // with a Source B path which would break the pipeline.
                            const loadedFilePath = this.currentLayerFilePath;
                            const loadedSource = this.layerSource.get(loadedFilePath) || 'A';
                            if (typeof window.setActiveFileFromViewer === 'function' &&
                                    loadedFilePath && loadedSource === 'A') {
                                window.setActiveFileFromViewer(loadedFilePath);
                            }
                        }, 500);
                    }
                }
            };
            
            // Start processing buildings in batches
            processBatch(0);
            
        } catch (error) {
            console.error('Error parsing CityJSON:', error);
            this.hideLoading();
            this.showError('Failed to parse CityJSON: ' + error.message);
        }
    }
    
    calculateGeographicBoundsFromBuildings() {
        // Use the geographic bounds we tracked while creating entities (most accurate)
        // These are already in WGS84 since we converted them with fromDegrees
        if (this.geographicBounds && 
            this.geographicBounds.minLat !== Infinity && 
            this.geographicBounds.minLon !== Infinity) {
            
            console.log('Using tracked geographic bounds from entity positions:', this.geographicBounds);
            
            return {
                min: { 
                    lat: this.geographicBounds.minLat, 
                    lon: this.geographicBounds.minLon 
                },
                max: { 
                    lat: this.geographicBounds.maxLat, 
                    lon: this.geographicBounds.maxLon 
                },
                center: {
                    lat: (this.geographicBounds.minLat + this.geographicBounds.maxLat) / 2,
                    lon: (this.geographicBounds.minLon + this.geographicBounds.maxLon) / 2
                }
            };
        }
        
        // Fallback to getGeographicBounds if tracking failed
        console.log('Tracked bounds not available, using fallback method');
        return this.getGeographicBounds();
    }
    
    transformToWGS84(x, y) {
        // Transform coordinates from source CRS to WGS84 (EPSG:4326)
        console.log(`Transforming coordinates: x=${x}, y=${y}, sourceCRS=${this.sourceCRS}`);
        
        if (!this.sourceCRS) {
            console.warn('No CRS specified, using approximation (may be inaccurate)');
            // No CRS info, use approximation
            const metersPerDegree = 111320.0;
            const result = {
                lon: this.defaultLocation.longitude + (x / metersPerDegree),
                lat: this.defaultLocation.latitude + (y / metersPerDegree)
            };
            console.log(`Approximation result:`, result);
            return result;
        }
        
        // Check if already WGS84
        if (this.sourceCRS.includes('4326') || this.sourceCRS.includes('WGS84')) {
            console.log('Already in WGS84, using coordinates directly');
            return { lon: x, lat: y };
        }
        
        // Use proj4js for transformation
        if (typeof proj4 !== 'undefined') {
            try {
                // Extract EPSG code from CRS string
                // Handle formats like: "EPSG:28992", "urn:ogc:def:crs:EPSG::28992", "28992", etc.
                let sourceEPSG = this.sourceCRS;
                
                // Extract EPSG number
                const epsgMatches = this.sourceCRS.match(/\d+/g);
                if (epsgMatches && epsgMatches.length > 0) {
                    const epsgCode = epsgMatches[epsgMatches.length - 1];
                    // EPSG:7415 is a compound CRS; use EPSG:28992 for horizontal transform.
                    if (epsgCode === '7415') {
                        sourceEPSG = 'EPSG:28992';
                    } else {
                        sourceEPSG = `EPSG:${epsgCode}`;
                    }
                } else if (!this.sourceCRS.includes('EPSG:')) {
                    sourceEPSG = `EPSG:${this.sourceCRS}`;
                }
                
                console.log(`Transforming from ${sourceEPSG} to EPSG:4326`);
                
                // Transform to WGS84
                const [lon, lat] = proj4(sourceEPSG, 'EPSG:4326', [x, y]);
                const result = { lon, lat };
                console.log(`Transformation result:`, result);
                return result;
            } catch (error) {
                // Silently fallback to approximation (don't log as error to reduce console noise)
                const metersPerDegree = 111320.0;
                return {
                    lon: this.defaultLocation.longitude + (x / metersPerDegree),
                    lat: this.defaultLocation.latitude + (y / metersPerDegree)
                };
            }
        } else {
            console.error('proj4js not loaded! Check if script is included in HTML.');
            // Fallback to approximation
            const metersPerDegree = 111320.0;
            return {
                lon: this.defaultLocation.longitude + (x / metersPerDegree),
                lat: this.defaultLocation.latitude + (y / metersPerDegree)
            };
        }
    }
    
    getGeographicBounds() {
        // Convert bounding box to geographic coordinates (lat/lon)
        if (!this.boundingBox) {
            console.warn('No bounding box available for geographic bounds calculation');
            return null;
        }
        
        console.log('Calculating geographic bounds from bounding box:', this.boundingBox);
        console.log('Source CRS:', this.sourceCRS);
        
        // Check if the bounding box is already in WGS84 (lat/lon)
        if (this.sourceCRS && (this.sourceCRS.includes('4326') || this.sourceCRS.includes('WGS84'))) {
            // Already in WGS84, just swap x/y to lon/lat
            console.log('Bounding box already in WGS84');
            return {
                min: { lat: this.boundingBox.min.y, lon: this.boundingBox.min.x },
                max: { lat: this.boundingBox.max.y, lon: this.boundingBox.max.x },
                center: {
                    lat: this.boundingBox.center.y,
                    lon: this.boundingBox.center.x
                }
            };
        }
        
        // Transform from source CRS to WGS84
        // Transform all four corners to ensure we get the correct bounding box
        const corners = [
            { x: this.boundingBox.min.x, y: this.boundingBox.min.y }, // SW
            { x: this.boundingBox.min.x, y: this.boundingBox.max.y }, // NW
            { x: this.boundingBox.max.x, y: this.boundingBox.min.y }, // SE
            { x: this.boundingBox.max.x, y: this.boundingBox.max.y }  // NE
        ];
        
        const transformedCorners = corners.map(corner => this.transformToWGS84(corner.x, corner.y));
        
        // Find min/max from transformed corners
        let minLat = Math.min(...transformedCorners.map(c => c.lat));
        let maxLat = Math.max(...transformedCorners.map(c => c.lat));
        let minLon = Math.min(...transformedCorners.map(c => c.lon));
        let maxLon = Math.max(...transformedCorners.map(c => c.lon));
        
        const result = {
            min: { lat: minLat, lon: minLon },
            max: { lat: maxLat, lon: maxLon },
            center: {
                lat: (minLat + maxLat) / 2,
                lon: (minLon + maxLon) / 2
            }
        };
        
        console.log('Transformed geographic bounds:', result);
        return result;
    }
    
    calculateBoundingBox(vertices, transform) {
        if (!vertices || vertices.length === 0) return;
        
        let minX = Infinity, minY = Infinity, minZ = Infinity;
        let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
        
        vertices.forEach(vertex => {
            let [x, y, z] = vertex;
            
            // Apply transform if available
            if (transform) {
                x = x * transform.scale[0] + transform.translate[0];
                y = y * transform.scale[1] + transform.translate[1];
                z = z * transform.scale[2] + transform.translate[2];
            }
            
            minX = Math.min(minX, x);
            minY = Math.min(minY, y);
            minZ = Math.min(minZ, z);
            maxX = Math.max(maxX, x);
            maxY = Math.max(maxY, y);
            maxZ = Math.max(maxZ, z);
        });
        
        this.boundingBox = {
            min: { x: minX, y: minY, z: minZ },
            max: { x: maxX, y: maxY, z: maxZ },
            center: {
                x: (minX + maxX) / 2,
                y: (minY + maxY) / 2,
                z: (minZ + maxZ) / 2
            }
        };
    }
    
    createBuildingEntity(objectId, cityObject, geometry, vertices, transform) {
        try {
            // Single vertex pass: compute footprint positions + height in one sweep.
            // (Previously calculateBuildingBoundingBox + convertGeometryToPositions ran separately,
            //  each iterating all vertices plus proj4 per vertex in the second pass.)
            const useTransform = transform !== null;
            const processed = this._processBuilding(geometry, vertices, useTransform ? transform : null);

            if (!processed) {
                console.warn(`Skipping entity ${objectId}: could not process geometry`);
                return null;
            }

            const { positions, height } = processed;

            if (!positions || positions.length < 3) {
                console.warn(`Skipping entity ${objectId}: insufficient positions (${positions ? positions.length : 0})`);
                return null;
            }
            if (height <= 0) {
                console.warn(`Skipping entity ${objectId}: invalid height ${height}`);
                return null;
            }
            
            // Create Cesium entity
            // Let Cesium auto-generate unique IDs to avoid duplicate ID errors.
            // description is generated lazily on click — no HTML string creation per building at load time.
            const defaultColor = this.getMaterialForObjectType(cityObject.type, this.currentLayerSource);
            const entity = this.viewer.entities.add({
                name: cityObject.attributes?.name || objectId,
                buildingId: objectId,
                filePath: this.currentLayerFilePath,
                cityObjectData: cityObject,
                defaultColor,  // cached for reliable restore in applyLayerVisualStyles
                polygon: {
                    hierarchy: positions,
                    extrudedHeight: height,
                    height: 0,
                    material: new Cesium.ColorMaterialProperty(defaultColor),
                    outline: true,
                    outlineColor: Cesium.Color.BLACK.withAlpha(0.3),
                    outlineWidth: 1
                }
            });
            
            // Store entity for click handling
            if (!this.buildingEntities.has(objectId)) {
                this.buildingEntities.set(objectId, []);
                // Pre-compute ID mapping for fast lookups
                this._updateIdMapping(objectId);
            }
            this.buildingEntities.get(objectId).push(entity);

            if (this.currentLayerFilePath) {
                if (!this.layerEntities.has(this.currentLayerFilePath)) {
                    this.layerEntities.set(this.currentLayerFilePath, []);
                }
                this.layerEntities.get(this.currentLayerFilePath).push(entity);
                if (this.currentLayerSource) {
                    this.layerSource.set(this.currentLayerFilePath, this.currentLayerSource);
                }
            }
            
            return entity;
        } catch (error) {
            console.error(`Error in createBuildingEntity for ${objectId}:`, error);
            console.error('Error stack:', error.stack);
            return null;
        }
    }
    
    /**
     * Single-pass geometry processor: collects footprint WGS84 positions AND
     * computes minZ/maxZ in one vertex traversal instead of two separate passes.
     * Returns { positions: Cartesian3[], height: number } or null on failure.
     * @private
     */
    _processBuilding(geometry, vertices, transform) {
        try {
            let footprintRing = null;

            if (geometry.type === 'Solid' && geometry.boundaries) {
                const outerShell = geometry.boundaries[0];
                if (outerShell && outerShell.length > 0 && outerShell[0] && outerShell[0].length > 0) {
                    footprintRing = outerShell[0][0]; // first face, first ring
                }
            } else if (geometry.type === 'MultiSurface' && geometry.boundaries && geometry.boundaries.length > 0) {
                const first = geometry.boundaries[0];
                if (first && first.length > 0) footprintRing = first[0];
            }

            if (!footprintRing || footprintRing.length === 0) return null;

            // Pass 1 of 1: iterate footprint vertices to build WGS84 positions
            // AND track Z min/max across the footprint ring.
            let minZ = Infinity, maxZ = -Infinity;
            const positions = [];
            const n = vertices.length;

            for (let i = 0; i < footprintRing.length; i++) {
                const idx = footprintRing[i];
                if (idx < 0 || idx >= n) continue;
                const v = vertices[idx];
                let x = v[0], y = v[1], z = v[2];
                if (transform) {
                    x = x * transform.scale[0] + transform.translate[0];
                    y = y * transform.scale[1] + transform.translate[1];
                    z = z * transform.scale[2] + transform.translate[2];
                }
                if (z < minZ) minZ = z;
                if (z > maxZ) maxZ = z;
                const coords = this.transformToWGS84(x, y);
                positions.push(Cesium.Cartesian3.fromDegrees(coords.lon, coords.lat, minZ));
            }

            if (positions.length < 3 || minZ === Infinity) return null;

            // For Solid geometries we also need the real maxZ across all faces to get a
            // correct building height — iterate the remaining vertices (Z only, no proj4).
            if (geometry.type === 'Solid' && geometry.boundaries) {
                const outerShell = geometry.boundaries[0];
                outerShell.forEach(face => {
                    face.forEach(ring => {
                        ring.forEach(idx => {
                            if (idx < 0 || idx >= n) return;
                            const v = vertices[idx];
                            let z = v[2];
                            if (transform) z = z * transform.scale[2] + transform.translate[2];
                            if (z < minZ) minZ = z;
                            if (z > maxZ) maxZ = z;
                        });
                    });
                });
            }

            // Re-set the base height of all positions now that we know the true minZ
            for (let i = 0; i < positions.length; i++) {
                const cart = Cesium.Cartographic.fromCartesian(positions[i]);
                positions[i] = Cesium.Cartesian3.fromRadians(cart.longitude, cart.latitude, minZ);
            }

            return { positions, height: maxZ - minZ };
        } catch (err) {
            console.error('_processBuilding error:', err);
            return null;
        }
    }

    calculateBuildingBoundingBox(geometry, vertices, transform) {
        // Calculate bounding box for this specific building's geometry
        let minX = Infinity, minY = Infinity, minZ = Infinity;
        let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
        
        try {
            const processVertex = (vertex) => {
                // If vertices are already transformed, transform is null
                // Otherwise, apply transform
                let [x, y, z] = vertex;
                
                if (transform) {
                    x = x * transform.scale[0] + transform.translate[0];
                    y = y * transform.scale[1] + transform.translate[1];
                    z = z * transform.scale[2] + transform.translate[2];
                }
                
                minX = Math.min(minX, x);
                minY = Math.min(minY, y);
                minZ = Math.min(minZ, z);
                maxX = Math.max(maxX, x);
                maxY = Math.max(maxY, y);
                maxZ = Math.max(maxZ, z);
            };
            
            if (geometry.type === 'Solid' && geometry.boundaries) {
                const outerShell = geometry.boundaries[0];
                if (outerShell && outerShell.length > 0) {
                    outerShell.forEach(face => {
                        if (face && face.length > 0) {
                            face.forEach(ring => {
                                if (ring && ring.length > 0) {
                                    ring.forEach(vertexIdx => {
                                        if (vertexIdx >= 0 && vertexIdx < vertices.length) {
                                            processVertex(vertices[vertexIdx]);
                                        }
                                    });
                                }
                            });
                        }
                    });
                }
            } else if (geometry.type === 'MultiSurface' && geometry.boundaries) {
                geometry.boundaries.forEach(surface => {
                    if (surface && surface.length > 0) {
                        surface.forEach(ring => {
                            if (ring && ring.length > 0) {
                                ring.forEach(vertexIdx => {
                                    if (vertexIdx >= 0 && vertexIdx < vertices.length) {
                                        processVertex(vertices[vertexIdx]);
                                    }
                                });
                            }
                        });
                    }
                });
            }
            
            if (minX === Infinity) {
                return null; // No vertices found
            }
            
            return {
                min: { x: minX, y: minY, z: minZ },
                max: { x: maxX, y: maxY, z: maxZ }
            };
        } catch (error) {
            console.error('Error calculating building bounding box:', error);
            return null;
        }
    }
    
    convertGeometryToPositions(geometry, vertices, transform, baseHeight = 0) {
        const positions = [];
        
        try {
            if (geometry.type === 'Solid' && geometry.boundaries) {
                // Get the outer shell (first boundary)
                const outerShell = geometry.boundaries[0];
                
                if (outerShell && outerShell.length > 0) {
                    // Get the first face's first ring (footprint)
                    const firstFace = outerShell[0];
                    if (firstFace && firstFace.length > 0) {
                        const firstRing = firstFace[0];
                        
                        // Convert vertex indices to Cesium positions
                        firstRing.forEach(vertexIdx => {
                            if (vertexIdx >= 0 && vertexIdx < vertices.length) {
                                // Vertices are already transformed if transform is null
                                const vertex = vertices[vertexIdx];
                                
                                // Convert coordinates to WGS84 (lat/lon) for Cesium
                                const coords = this.transformToWGS84(vertex[0], vertex[1]);
                                
                                // Use baseHeight for footprint (ground level)
                                positions.push(Cesium.Cartesian3.fromDegrees(coords.lon, coords.lat, baseHeight));
                            }
                        });
                    }
                }
            } else if (geometry.type === 'MultiSurface' && geometry.boundaries) {
                // Handle MultiSurface - use first surface
                if (geometry.boundaries.length > 0) {
                    const firstSurface = geometry.boundaries[0];
                    if (firstSurface && firstSurface.length > 0) {
                        const firstRing = firstSurface[0];
                        
                        firstRing.forEach(vertexIdx => {
                            if (vertexIdx >= 0 && vertexIdx < vertices.length) {
                                // Vertices are already transformed if transform is null
                                const vertex = vertices[vertexIdx];
                                
                                // Convert coordinates to WGS84 (lat/lon) for Cesium
                                const coords = this.transformToWGS84(vertex[0], vertex[1]);
                                
                                // Use baseHeight for footprint (ground level)
                                positions.push(Cesium.Cartesian3.fromDegrees(coords.lon, coords.lat, baseHeight));
                            }
                        });
                    }
                }
            }
        } catch (error) {
            console.error('Error converting geometry:', error);
            return null;
        }
        
        return positions.length >= 3 ? positions : null;
    }
    
    // This function is no longer used - height is now calculated per-building
    // Keeping for backwards compatibility
    getBuildingHeight(cityObject, geometry, vertices, transform) {
        // Try to get height from attributes
        if (cityObject.attributes) {
            if (cityObject.attributes.measuredHeight) {
                return cityObject.attributes.measuredHeight;
            }
            if (cityObject.attributes.height) {
                return cityObject.attributes.height;
            }
        }
        
        // Default height (fallback)
        return 10;
    }
    
    getMaterialForObjectType(objectType, source) {
        // Returns a pre-computed, shared Color constant — no allocation on each call.
        const palette = _MAT[source === 'B' ? 'B' : 'A'];
        return palette[objectType] || palette._fallback;
    }
    
    /**
     * Update ID mapping for fast lookups
     * @private
     */
    _updateIdMapping(objectId) {
        const numericMatch = objectId.match(/(\d{10,})/);
        if (numericMatch) {
            const numericId = numericMatch[1];
            if (!this.idMapping.has(numericId)) {
                this.idMapping.set(numericId, []);
            }
            this.idMapping.get(numericId).push(objectId);
            // Also add variations
            if (objectId.startsWith('bag_')) {
                this.idMapping.get(numericId).push(objectId.replace('bag_', ''));
            } else {
                this.idMapping.get(numericId).push(`bag_${objectId}`);
            }
        }
    }
    
    /**
     * Fast lookup of entities by any ID variation
     * @private
     */
    _findEntitiesById(buildingId) {
        // Direct lookup first (fastest)
        let entities = this.buildingEntities.get(buildingId);
        if (entities && entities.length > 0) {
            return entities;
        }
        
        // Use pre-computed ID mapping
        const numericMatch = buildingId.match(/(\d{10,})/);
        if (numericMatch) {
            const numericId = numericMatch[1];
            const idVariations = this.idMapping.get(numericId);
            if (idVariations) {
                for (const variation of idVariations) {
                    entities = this.buildingEntities.get(variation);
                    if (entities && entities.length > 0) {
                        return entities;
                    }
                }
            }
        }
        
        return null;
    }
    
    /**
     * Update building color based on pipeline stage
     * @param {string} buildingId - Building ID to update
     * @param {string} colorName - Color name: 'blue', 'orange', 'yellow', 'green', 'red', 'darkgray'
     */
    updateBuildingColor(buildingId, colorName) {
        const newColor = BUILDING_COLOR_MAP[colorName] || BUILDING_COLOR_MAP['blue'];
        
        // Fast lookup using pre-computed mapping
        const entities = this._findEntitiesById(buildingId);
        
        if (entities && entities.length > 0) {
            entities.forEach(entity => {
                if (entity.polygon) {
                    entity.originalMaterial = newColor;
                    entity.polygon.material = newColor;
                }
            });
            return true;
        }
        
        return false;
    }
    
    /**
     * Update colors for multiple buildings at once.
     * When selectedFilePath is set and multiple layers exist, only the selected layer gets full fill colors;
     * other layers use a semi-transparent fill + distinct outline (contour) so overlapping buildings are visible.
     * @param {Object} buildingColors - Map of buildingId -> colorName
     * @param {string} [selectedFilePath] - If set and layer is loaded, apply status colors only to this layer; others get outline style
     * @returns {Promise} Resolves when all color updates are complete
     */
    updateBuildingColors(buildingColors, selectedFilePath) {
        return new Promise((resolve) => {
            const hasMultipleLayers = this.layerEntities.size > 1;
            const useLayerSelection = selectedFilePath && hasMultipleLayers && this.layerEntities.has(selectedFilePath);
            const defaultColorForSelected = useLayerSelection
                ? this.getMaterialForObjectType('Building', this.layerSource.get(selectedFilePath))
                : BUILDING_COLOR_MAP['blue'];
            const updateStartTime = performance.now();

            // Helper: apply color to a single entity. Outline properties are only
            // written once (they never change between pipeline stages).
            const applyColor = (entity, color, outlineColor, outlineWidth) => {
                if (!entity.polygon) return;
                entity.originalMaterial = color;
                entity.polygon.material = color;
                if (!entity._outlineApplied) {
                    entity.polygon.outline = true;
                    entity.polygon.outlineColor = outlineColor;
                    entity.polygon.outlineWidth = outlineWidth;
                    entity._outlineApplied = true;
                }
            };

            if (useLayerSelection) {
                // Apply pipeline colors only to the selected layer. Other layers keep their
                // current colors — auto-dimming is removed; only the ◑ button dims layers.
                this.layerEntities.forEach((entities, filePath) => {
                    if (filePath !== selectedFilePath) return;
                    entities.forEach(entity => {
                        if (!entity.polygon) return;
                        const colorName = buildingColors[entity.buildingId];
                        const color = colorName
                            ? (BUILDING_COLOR_MAP[colorName] || BUILDING_COLOR_MAP['blue'])
                            : defaultColorForSelected;
                        applyColor(entity, color, SELECTED_LAYER_OUTLINE, 1.5);
                    });
                });
            } else {
                if (Object.keys(buildingColors).length === 0) {
                    resolve();
                    return;
                }
                let applied = false;
                Object.entries(buildingColors).forEach(([buildingId, colorName]) => {
                    const newColor = BUILDING_COLOR_MAP[colorName] || BUILDING_COLOR_MAP['blue'];
                    const entities = this._findEntitiesById(buildingId);
                    if (entities && entities.length > 0) {
                        applied = true;
                        entities.forEach(entity => {
                            applyColor(entity, newColor, SELECTED_LAYER_OUTLINE, 1.5);
                        });
                    }
                });
                if (!applied) {
                    resolve();
                    return;
                }
            }
            
            // Force Cesium to render immediately
            if (this.viewer && this.viewer.scene) {
                this.viewer.scene.requestRender();
                
                // Wait for Cesium's postRender event to know when rendering is complete.
                // One render cycle is sufficient to confirm the GPU has the new material data.
                let renderCount = 0;
                let resolved = false;
                const maxRenders = 1;    // was 3 — one frame is enough
                const maxWaitTime = 150; // was 500 ms
                
                const resolveOnce = () => {
                    if (!resolved) {
                        resolved = true;
                        const updateTime = performance.now() - updateStartTime;
                        console.log(`Color updates applied in ${updateTime.toFixed(2)}ms`);
                        resolve();
                    }
                };
                
                // Listen for postRender events
                const postRenderHandler = () => {
                    renderCount++;
                    if (renderCount >= maxRenders) {
                        this.viewer.scene.postRender.removeEventListener(postRenderHandler);
                        resolveOnce();
                    }
                };
                
                this.viewer.scene.postRender.addEventListener(postRenderHandler);
                
                // Timeout as backup
                setTimeout(() => {
                    if (!resolved) {
                        this.viewer.scene.postRender.removeEventListener(postRenderHandler);
                        resolveOnce();
                    }
                }, maxWaitTime);
            } else {
                // Fallback if scene not available
                setTimeout(() => {
                    resolve();
                }, 100);
            }
        });
    }
    
    createBuildingDescription(cityObject) {
        let html = '<table class="cesium-infoBox-defaultTable">';
        html += `<tr><th>Type</th><td>${cityObject.type || 'Unknown'}</td></tr>`;
        
        if (cityObject.attributes) {
            Object.keys(cityObject.attributes).forEach(key => {
                const value = cityObject.attributes[key];
                html += `<tr><th>${key}</th><td>${value}</td></tr>`;
            });
        }
        
        html += '</table>';
        return html;
    }
    
    zoomToLayer(filePath) {
        if (!filePath || !this.viewer) return;
        const entities = this.layerEntities.get(filePath) || [];
        if (entities.length === 0) {
            console.warn('No entities found for layer:', filePath);
            return;
        }
        this.viewer.flyTo(entities, {
            duration: 1.5,
            offset: new Cesium.HeadingPitchRange(
                0,
                Cesium.Math.toRadians(-60),
                0
            )
        });
    }

    fitCameraToBuildings() {
        // Suppress auto-fly during tutorial steps that have their own camera control
        if (window.tutorialSuppressAutoFly) return;

        // Get all building entities
        const entities = [];
        this.buildingEntities.forEach(entityArray => {
            entities.push(...entityArray);
        });
        
        if (entities.length === 0) {
            // Use bounding box if available
            if (this.boundingBox) {
                const center = this.boundingBox.center;
                const coords = this.transformToWGS84(center.x, center.y);
                const height = Math.max(
                    Math.abs(this.boundingBox.max.x - this.boundingBox.min.x),
                    Math.abs(this.boundingBox.max.y - this.boundingBox.min.y)
                ) * 1.5;
                
                this.viewer.camera.flyTo({
                    destination: Cesium.Cartesian3.fromDegrees(coords.lon, coords.lat, height),
                    orientation: {
                        heading: Cesium.Math.toRadians(0),
                        pitch: Cesium.Math.toRadians(-90), // Top-down view
                        roll: 0.0
                    },
                    duration: 2.0
                }).then(() => {
                    // Store initial camera position after camera animation completes
                    // Wait longer to ensure camera has fully settled
                    if (!this.isComparisonViewer && !this.initialCameraPosition) {
                        setTimeout(() => {
                            this.storeInitialCameraPosition();
                        }, 500);
                    }
                });
            }
            return;
        }
        
        // Use Cesium's built-in flyTo for all entities - Top-down view
        this.viewer.flyTo(entities, {
            duration: 2.0,
            offset: new Cesium.HeadingPitchRange(
                0,
                Cesium.Math.toRadians(-90), // Top-down view (-90 degrees = looking straight down)
                0
            )
        }).then(() => {
            // Store initial camera position after camera animation completes
            // Wait longer to ensure camera has fully settled
            if (!this.isComparisonViewer && !this.initialCameraPosition) {
                setTimeout(() => {
                    this.storeInitialCameraPosition();
                }, 500);
            }
        });
    }
    
    clearBuildings() {
        this.buildingEntities.forEach(entityArray => {
            entityArray.forEach(entity => {
                this.viewer.entities.remove(entity);
            });
        });
        this.buildingEntities.clear();
        this.idMapping.clear(); // Clear ID mapping cache
        this.cityObjects = {};
        this.layerEntities.clear();
        this.layerSource.clear();
        this.cityObjectsByFile.clear();
        this.boundingBox = null;
        this.crs = null;
        this.sourceCRS = null;
        this.currentLayerFilePath = null;
        this.pendingLoads = [];
        this.initialCameraPosition = null; // Clear initial camera position when clearing buildings
    }
    
    clearPlaceholder() {
        const placeholder = this.container.querySelector('.placeholder');
        if (placeholder) {
            placeholder.remove();
        }
    }

    /** Inject zoom/rotate/tilt/home buttons for touch-friendly map control */
    _injectMapControls() {
        if (this.container.querySelector('#viewer-map-controls')) return;
        const wrap = document.createElement('div');
        wrap.id = 'viewer-map-controls';
        wrap.className = 'viewer-map-controls';
        wrap.setAttribute('aria-label', 'Map controls');
        const buttons = [
            { fn: 'zoomInViewer', title: 'Zoom in', label: 'Zoom in', sym: '+', cls: '' },
            { fn: 'zoomOutViewer', title: 'Zoom out', label: 'Zoom out', sym: '−', cls: '' },
            { fn: 'rotateViewerLeft', title: 'Rotate left', label: 'Rotate left', sym: '↶', cls: '' },
            { fn: 'rotateViewerRight', title: 'Rotate right', label: 'Rotate right', sym: '↷', cls: '' },
            { fn: 'tiltViewerUp', title: 'Tilt up (top-down)', label: 'Tilt up', sym: '⌃', cls: '' },
            { fn: 'tiltViewerDown', title: 'Tilt down (horizon)', label: 'Tilt down', sym: '⌄', cls: '' },
            { fn: 'resetViewerCamera', title: 'Reset view', label: 'Reset view', sym: '⌂', cls: 'viewer-map-control-btn-home' }
        ];
        buttons.forEach(({ fn, title, label, sym, cls }) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'viewer-map-control-btn' + (cls ? ' ' + cls : '');
            btn.title = title;
            btn.setAttribute('aria-label', label);
            btn.textContent = sym;
            btn.addEventListener('click', () => { if (typeof window[fn] === 'function') window[fn](); });
            wrap.appendChild(btn);
        });
        this.container.appendChild(wrap);
    }

    removeLayer(filePath) {
        if (!filePath || !this.viewer) {
            return;
        }
        const entities = this.layerEntities.get(filePath) || [];
        entities.forEach((entity) => {
            this.viewer.entities.remove(entity);
        });
        this.layerEntities.delete(filePath);
        this.layerSource.delete(filePath);
        this.cityObjectsByFile.delete(filePath);

        this.buildingEntities.forEach((entityArray, buildingId) => {
            const remaining = entityArray.filter((entity) => entity.filePath !== filePath);
            if (remaining.length > 0) {
                this.buildingEntities.set(buildingId, remaining);
            } else {
                this.buildingEntities.delete(buildingId);
            }
        });
    }
    
    showLoading() {
        console.log('Loading CityJSON...');
        // Show visual loading indicator
        if (this.container) {
            const existing = document.getElementById('cesium-loading-indicator');
            if (existing) {
                existing.remove();
            }
            const loadingDiv = document.createElement('div');
            loadingDiv.id = 'cesium-loading-indicator';
            loadingDiv.style.cssText = `
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                background: rgba(0, 0, 0, 0.8);
                color: white;
                padding: 20px 30px;
                border-radius: 8px;
                z-index: 10000;
                text-align: center;
                font-family: Arial, sans-serif;
            `;
            loadingDiv.innerHTML = `
                <div style="font-size: 16px; margin-bottom: 10px;">Loading CityJSON...</div>
                <div id="cesium-loading-progress" style="font-size: 14px; color: #ccc;">Parsing data...</div>
            `;
            this.container.appendChild(loadingDiv);
        }
    }
    
    updateLoadingProgress(message) {
        const progressEl = document.getElementById('cesium-loading-progress');
        if (progressEl) {
            progressEl.textContent = message;
        }
    }
    
    hideLoading() {
        console.log('CityJSON loaded');
        // Remove loading indicator
        const loadingDiv = document.getElementById('cesium-loading-indicator');
        if (loadingDiv) {
            loadingDiv.remove();
        }
        this.isLoading = false;

        if (this.pendingLoads.length > 0) {
            const nextLoad = this.pendingLoads.shift();
            setTimeout(() => this.loadCityJSON(nextLoad.filePath, nextLoad.options), 0);
        } else {
            // All queued layers have finished loading — refresh visual styles
            if (typeof window.applyViewerLayerStyles === 'function') {
                window.applyViewerLayerStyles();
            }
        }
    }

    /**
     * Apply fill/outline visual styles based on how many layers are loaded.
     * - Single layer: full opaque fill + subtle outline (normal view).
     * - Multiple layers: selected layer → full fill + black outline;
     *                    other layers → semi-transparent fill + dark outline (contour-only look).
     * Call this whenever layers are added/removed or the active selection changes.
     * @param {string} [selectedFilePath]
     */
    applyLayerVisualStyles(selectedFilePath) {
        if (!this.viewer) return;

        this.layerEntities.forEach((entities, filePath) => {
            const uiState = (window.layerState && window.layerState[filePath]) || {};
            const isDimmed = !!uiState.dimmed;

            // A layer is inactive only when the user has explicitly dimmed it via the ◑ button.
            // There is no auto-dimming of non-selected layers.
            const isActive = !isDimmed;

            entities.forEach(entity => {
                if (!entity.polygon) return;
                if (isActive) {
                    // Restore the entity's default color.
                    // Priority: pipeline-applied color (originalMaterial) → per-entity cached default → source palette.
                    // Always wrap in a NEW ColorMaterialProperty so Cesium never skips the update
                    // due to same-reference optimisation on shared _MAT constants.
                    const color = entity.originalMaterial instanceof Cesium.Color
                        ? entity.originalMaterial
                        : (entity.defaultColor
                            || this.getMaterialForObjectType('Building', this.layerSource.get(filePath)));
                    entity.polygon.material = new Cesium.ColorMaterialProperty(color);
                    entity.polygon.outline = true;
                    entity.polygon.outlineColor = SELECTED_LAYER_OUTLINE;
                } else {
                    entity.polygon.material = new Cesium.ColorMaterialProperty(INACTIVE_LAYER_FILL);
                    entity.polygon.outline = true;
                    entity.polygon.outlineColor = INACTIVE_LAYER_OUTLINE;
                }
            });
        });

        if (this.viewer.scene) this.viewer.scene.requestRender();
    }

    /**
     * Show or hide all entities in a layer without removing them from the viewer.
     * Used by the legend eye-toggle so layers can be hidden without a full reload.
     */
    setLayerEntityShow(filePath, show) {
        const entities = this.layerEntities.get(filePath);
        if (!entities) return;
        entities.forEach(entity => { entity.show = show; });
        if (this.viewer && this.viewer.scene) this.viewer.scene.requestRender();
    }
    
    // ── Building markers (candidate / match / false-positive overlays) ─────────

    _getEntityCentroid(entity) {
        if (!entity.polygon || !entity.polygon.hierarchy) return null;
        try {
            const hier = entity.polygon.hierarchy.getValue(Cesium.JulianDate.now());
            if (!hier || !hier.positions || hier.positions.length === 0) return null;
            let x = 0, y = 0, z = 0;
            hier.positions.forEach(p => { x += p.x; y += p.y; z += p.z; });
            const n = hier.positions.length;
            const centroid = new Cesium.Cartesian3(x / n, y / n, z / n);
            const cart = Cesium.Cartographic.fromCartesian(centroid);
            return Cesium.Cartesian3.fromRadians(cart.longitude, cart.latitude, cart.height + 30);
        } catch (_) { return null; }
    }

    /**
     * Compute a WGS84 position from a raw CityJSON object (for buildings not in the Cesium viewer).
     */
    _getCentroidFromCityJSON(cityJSON) {
        if (!cityJSON) return null;
        try {
            const vertices   = cityJSON.vertices || [];
            const transform  = cityJSON.transform || null;
            const cityObjs   = cityJSON.CityObjects || {};
            const objectId   = Object.keys(cityObjs)[0];
            if (!objectId) return null;
            const geoms = cityObjs[objectId].geometry || [];
            if (geoms.length === 0) return null;

            // Apply CityJSON transform (scale + translate)
            const realVerts = transform
                ? vertices.map(v => [
                    v[0] * transform.scale[0] + transform.translate[0],
                    v[1] * transform.scale[1] + transform.translate[1],
                    v[2] * transform.scale[2] + transform.translate[2]
                  ])
                : vertices;

            // Collect all unique vertex indices from the first geometry
            const indices = new Set();
            const collect = (b) => {
                if (typeof b === 'number') indices.add(b);
                else if (Array.isArray(b)) b.forEach(collect);
            };
            collect(geoms[0].boundaries);
            if (indices.size === 0) return null;

            let sx = 0, sy = 0, sz = 0;
            indices.forEach(i => { if (realVerts[i]) { sx += realVerts[i][0]; sy += realVerts[i][1]; sz += realVerts[i][2]; } });
            const n = indices.size;
            const cx = sx / n, cy = sy / n, cz = sz / n;

            // Detect source CRS
            let sourceCRS = 'EPSG:28992';
            const rs = cityJSON.metadata?.referenceSystem || '';
            const m  = rs.match(/EPSG[::]+(\d+)/);
            if (m) sourceCRS = m[1] === '7415' ? 'EPSG:28992' : `EPSG:${m[1]}`;

            if (typeof proj4 === 'undefined') return null;
            const [lon, lat] = proj4(sourceCRS, 'EPSG:4326', [cx, cy]);
            return Cesium.Cartesian3.fromDegrees(lon, lat, cz + 30);
        } catch (_) { return null; }
    }

    /**
     * Place coloured label pins above the candidate and its BKAFI pairs, and draw
     * arrows from the candidate to each pair.
     * @param {string} candidateId
     * @param {Array}  pairs        – same array passed to showClassifierResultsInComparisonWindow
     * @param {Array}  pairCityData – [{buildingId, cityJSON}] fetched during the comparison window
     */
    addBuildingMarkers(candidateId, pairs, pairCityData = []) {
        this.clearBuildingMarkers();
        if (!this.viewer) return;
        this.buildingMarkers = [];

        // Lift a Cartesian3 position by N metres above the ground
        const elevate = (pos, metres) => {
            try {
                const c = Cesium.Cartographic.fromCartesian(pos);
                c.height += metres;
                return Cesium.Cartesian3.fromRadians(c.longitude, c.latitude, c.height);
            } catch (_) { return pos; }
        };

        // ── SVG pin icon builder ──────────────────────────────────────────────
        // Returns a data-URI for a clean circular pin (circle + bottom pointer).
        // symbol: unicode string shown inside (e.g. '✓', '✕', 'C')
        // bgHex:  CSS hex colour for the fill (e.g. '#15803d')
        const makePinDataUrl = (symbol, bgHex) => {
            const size = 44, r = 22, tailY = 54;
            const svg = [
                `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${tailY}">`,
                `<defs><filter id="ds" x="-30%" y="-30%" width="160%" height="160%">`,
                `<feDropShadow dx="0" dy="2" stdDeviation="2.5" flood-color="#000" flood-opacity="0.35"/></filter></defs>`,
                // circle body
                `<circle cx="${r}" cy="${r}" r="${r - 2}" fill="${bgHex}" stroke="white" stroke-width="2.5" filter="url(#ds)"/>`,
                // bottom pointer / tail
                `<polygon points="${r - 8},${size - 3} ${r + 8},${size - 3} ${r},${tailY}" fill="${bgHex}"/>`,
                // white border on tail sides to match circle stroke
                `<line x1="${r - 8}" y1="${size - 3}" x2="${r}" y2="${tailY}" stroke="white" stroke-width="1.5"/>`,
                `<line x1="${r + 8}" y1="${size - 3}" x2="${r}" y2="${tailY}" stroke="white" stroke-width="1.5"/>`,
                // symbol text
                `<text x="${r}" y="${r + 7}" text-anchor="middle" fill="white" `,
                `font-size="18" font-weight="bold" font-family="Arial,sans-serif">${symbol}</text>`,
                `</svg>`
            ].join('');
            return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
        };

        // ── Label / colour info ───────────────────────────────────────────────
        // Stack order (bottom → top): pair[n] … pair[0] … Candidate
        // All pins are centered (pixelOffsetX: 0).
        // Pair pins are on different buildings so only a small step is needed to
        // separate labels when two pairs happen to be very close.
        // The Candidate is fixed at 55 m — high enough to clear pair[0] at the
        // same location (True Match case) but not so high it floats above the city.
        const PAIR_BASE_LIFT = 55;   // metres for the first (lowest) pair
        const PAIR_STEP      = 18;   // metres between consecutive pair pins
        const CANDIDATE_LIFT = 100;  // fixed — independent of pair count

        const info = {};
        (pairs || []).forEach((pair, idx) => {
            const pred = pair.prediction !== undefined ? pair.prediction : (pair.confidence > 0.5 ? 1 : 0);
            const tl   = pair.true_label !== undefined && pair.true_label !== null ? pair.true_label : null;
            let text, symbol, bgHex, bg, lineColor;
            if (tl === 1) {
                text      = 'True Match';
                symbol    = '\u2713';          // ✓
                bgHex     = '#15803d';
                bg        = Cesium.Color.fromCssColorString(bgHex).withAlpha(0.92);
                lineColor = Cesium.Color.fromCssColorString('#4ade80').withAlpha(0.9);
            } else if (pred === 1 && tl === 0) {
                text      = 'False Positive';
                symbol    = '!';
                bgHex     = '#b45309';
                bg        = Cesium.Color.fromCssColorString(bgHex).withAlpha(0.92);
                lineColor = Cesium.Color.fromCssColorString('#fbbf24').withAlpha(0.9);
            } else {
                text      = 'No Match';
                symbol    = '\u2715';          // ✕
                bgHex     = '#374151';
                bg        = Cesium.Color.fromCssColorString(bgHex).withAlpha(0.92);
                lineColor = Cesium.Color.fromCssColorString('#6b7280').withAlpha(0.85);
            }
            info[pair.index_id] = {
                text, symbol, bgHex, bg, lineColor,
                liftMetres:   PAIR_BASE_LIFT + idx * PAIR_STEP,
                pixelOffsetX: 0,
            };
        });

        // Candidate sits above the True Match (same building) at a fixed height
        info[candidateId] = {
            text:         'Candidate',
            symbol:       'C',
            bgHex:        '#1565c0',
            bg:           Cesium.Color.fromCssColorString('#1565c0').withAlpha(0.92),
            lineColor:    null,
            liftMetres:   CANDIDATE_LIFT,
            pixelOffsetX: 0,
        };

        // ── Collect ground-level centroids ────────────────────────────────────
        const groundPositions = {};
        this.layerEntities.forEach((entities) => {
            entities.forEach(entity => {
                const bid = entity.buildingId;
                if (bid && info[bid] && !groundPositions[bid]) {
                    const pos = this._getEntityCentroid(entity);
                    if (pos) groundPositions[bid] = pos;
                }
            });
        });
        pairCityData.forEach(pcd => {
            if (!pcd) return;
            const bid = pcd.buildingId;
            if (bid && info[bid] && !groundPositions[bid]) {
                const pos = this._getCentroidFromCityJSON(pcd.cityJSON);
                if (pos) groundPositions[bid] = pos;
            }
        });

        const candidateGround = groundPositions[candidateId];

        // ── Pin markers — custom SVG circle pins + text label below ──────────
        Object.entries(groundPositions).forEach(([bid, groundPos]) => {
            const { text, symbol, bgHex, bg, liftMetres, pixelOffsetX } = info[bid];
            const labelPos = elevate(groundPos, liftMetres);
            const pinDataUrl = makePinDataUrl(symbol, bgHex);
            const pinOffsetX = pixelOffsetX || 0;

            const pin = this.viewer.entities.add({
                position: labelPos,
                billboard: {
                    image:                    pinDataUrl,
                    verticalOrigin:           Cesium.VerticalOrigin.BOTTOM,
                    horizontalOrigin:         Cesium.HorizontalOrigin.CENTER,
                    disableDepthTestDistance: Number.POSITIVE_INFINITY,
                    scale:                    1.0,
                    heightReference:          Cesium.HeightReference.NONE,
                    pixelOffset:              new Cesium.Cartesian2(pinOffsetX, 0),
                },
                label: {
                    text,
                    font:                     'bold 12px Arial, sans-serif',
                    style:                    Cesium.LabelStyle.FILL_AND_OUTLINE,
                    fillColor:                Cesium.Color.WHITE,
                    outlineColor:             bg,
                    outlineWidth:             2,
                    showBackground:           true,
                    backgroundColor:          bg.withAlpha(0.85),
                    backgroundPadding:        new Cesium.Cartesian2(8, 4),
                    horizontalOrigin:         Cesium.HorizontalOrigin.CENTER,
                    verticalOrigin:           Cesium.VerticalOrigin.TOP,
                    pixelOffset:              new Cesium.Cartesian2(pinOffsetX, 6),
                    disableDepthTestDistance: Number.POSITIVE_INFINITY,
                },
            });
            pin.markerBuildingId = bid;
            pin.markerPosition   = labelPos;
            this.buildingMarkers.push(pin);

            // Vertical stem from near-ground (–28 m reverses the +30 already in groundPos)
            // up to the label, so the line visually touches the building roof
            const buildingBase = elevate(groundPos, -26);
            const stem = this.viewer.entities.add({
                polyline: {
                    positions:                [buildingBase, labelPos],
                    width:                    2,
                    material:                 bg.withAlpha(0.80),
                    clampToGround:            false,
                    disableDepthTestDistance: Number.POSITIVE_INFINITY,
                }
            });
            this.buildingMarkers.push(stem);
        });

        // ── Horizontal connector lines from candidate to each pair ────────────
        if (candidateGround) {
            (pairs || []).forEach(pair => {
                const pairGround = groundPositions[pair.index_id];
                if (!pairGround) return;
                const { lineColor, liftMetres } = info[pair.index_id];
                // Draw at the pair's label height for a clean horizontal look
                const fromPos = elevate(candidateGround, liftMetres);
                const toPos   = elevate(pairGround,      liftMetres);
                const line = this.viewer.entities.add({
                    polyline: {
                        positions: [fromPos, toPos],
                        width:     3,
                        material:  new Cesium.PolylineArrowMaterialProperty(lineColor),
                        clampToGround: false,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                    }
                });
                this.buildingMarkers.push(line);
            });
        }

        if (this.viewer.scene) this.viewer.scene.requestRender();
    }

    findEntityByBuildingId(buildingId) {
        let found = null;
        this.layerEntities.forEach((entities) => {
            if (found) return;
            entities.forEach(entity => {
                if (!found && entity.buildingId === buildingId) found = entity;
            });
        });
        return found;
    }

    clearBuildingMarkers() {
        if (!this.viewer || !this.buildingMarkers) return;
        this.buildingMarkers.forEach(m => { try { this.viewer.entities.remove(m); } catch (_) {} });
        this.buildingMarkers = [];
        if (this.viewer.scene) this.viewer.scene.requestRender();
    }

    showError(message) {
        // Add error display
        const errorDiv = document.createElement('div');
        errorDiv.className = 'cesium-error';
        errorDiv.style.cssText = `
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: rgba(220, 53, 69, 0.9);
            color: white;
            padding: 20px;
            border-radius: 8px;
            z-index: 10000;
            text-align: center;
        `;
        errorDiv.innerHTML = `
            <h4>Error Loading 3D Model</h4>
            <p>${message}</p>
            <button onclick="this.parentElement.remove()" style="margin-top: 10px; padding: 8px 16px; background: white; color: #dc3545; border: none; border-radius: 4px; cursor: pointer;">Close</button>
        `;
        this.container.appendChild(errorDiv);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            if (errorDiv.parentElement) {
                errorDiv.remove();
            }
        }, 5000);
    }
    
    // Store initial camera position after file is loaded
    storeInitialCameraPosition() {
        if (this.viewer && this.viewer.camera) {
            const camera = this.viewer.camera;
            // Store the current camera position and orientation
            // Use the actual camera position after it has settled
            this.initialCameraPosition = {
                destination: Cesium.Cartesian3.clone(camera.position),
                orientation: {
                    heading: camera.heading,
                    pitch: camera.pitch,
                    roll: camera.roll
                }
            };
            console.log('Stored initial camera position:', {
                position: Cesium.Cartographic.fromCartesian(camera.position),
                heading: camera.heading,
                pitch: camera.pitch
            });
        }
    }
    
    // Reset camera to initial position after first load
    resetCamera() {
        if (!this.viewer || !this.viewer.camera) {
            console.warn('Cannot reset camera: viewer not available');
            return;
        }
        
        // Always use fitCameraToBuildings to ensure buildings are visible
        // This is more reliable than storing/restoring camera position
        this.fitCameraToBuildings();
    }
    
    toggleFullscreen() {
        if (this.viewer && this.viewer.fullscreenButton) {
            this.viewer.fullscreenButton.viewModel.command();
        }
    }

    zoomIn() {
        if (!this.viewer || !this.viewer.camera) return;
        this.viewer.camera.zoomIn(this.viewer.camera.positionCartographic.height * 0.2);
    }

    zoomOut() {
        if (!this.viewer || !this.viewer.camera) return;
        this.viewer.camera.zoomOut(this.viewer.camera.positionCartographic.height * 0.2);
    }

    /** Rotate camera left (counter-clockwise) — uses Cesium's lookLeft for reliable rotation */
    rotateLeft() {
        if (!this.viewer || !this.viewer.camera) return;
        const angle = Cesium.Math.toRadians(15);
        this.viewer.camera.lookLeft(angle);
        if (this.viewer.scene) this.viewer.scene.requestRender();
    }

    /** Rotate camera right (clockwise) — uses Cesium's lookRight */
    rotateRight() {
        if (!this.viewer || !this.viewer.camera) return;
        const angle = Cesium.Math.toRadians(15);
        this.viewer.camera.lookRight(angle);
        if (this.viewer.scene) this.viewer.scene.requestRender();
    }

    /** Tilt down toward horizon (see more of building sides) — larger step for easier exploration */
    tiltDown() {
        if (!this.viewer || !this.viewer.camera) return;
        const angle = Cesium.Math.toRadians(12);
        this.viewer.camera.lookDown(angle);
        if (this.viewer.scene) this.viewer.scene.requestRender();
    }

    /** Tilt up toward top-down view */
    tiltUp() {
        if (!this.viewer || !this.viewer.camera) return;
        const angle = Cesium.Math.toRadians(12);
        this.viewer.camera.lookUp(angle);
        if (this.viewer.scene) this.viewer.scene.requestRender();
    }

    setBasemap(mode) {
        if (!this.viewer || !this.viewer.imageryLayers) return;
        if (mode === 'satellite' && this.ionImageryProvider) {
            this.viewer.imageryLayers.removeAll();
            this.viewer.imageryLayers.addImageryProvider(this.ionImageryProvider);
            return;
        }
        // Default to OSM
        if (this.osmImageryProvider) {
            this.viewer.imageryLayers.removeAll();
            this.viewer.imageryLayers.addImageryProvider(this.osmImageryProvider);
        }
    }
    
    zoomToModel() {
        this.fitCameraToBuildings();
    }
}

// Initialize when page loads
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM loaded, waiting for Cesium...');
    
    // Check if viewer container exists
    const viewerContainer = document.getElementById('viewer');
    if (!viewerContainer) {
        console.error('Viewer container element not found in DOM');
        return;
    }
    
    // Wait for Cesium to load
    let checkCount = 0;
    const maxChecks = 100; // 10 seconds total
    
    const checkCesium = setInterval(() => {
        checkCount++;
        
        if (typeof Cesium !== 'undefined') {
            clearInterval(checkCesium);
            console.log('Cesium loaded, initializing viewer...');
            
            try {
                // Small delay to ensure Cesium is fully ready
                setTimeout(() => {
                    try {
                        window.viewer = new CesiumCityJSONViewer('viewer');
                        console.log('Cesium viewer initialized successfully');
                    } catch (initError) {
                        console.error('Error creating Cesium viewer instance:', initError);
                        const viewer = document.getElementById('viewer');
                        if (viewer) {
                            viewer.innerHTML = `
                                <div class="placeholder">
                                    <div class="placeholder-icon">⚠️</div>
                                    <p>Error initializing 3D viewer: ${initError.message}</p>
                                    <p style="font-size: 12px; margin-top: 10px;">Check browser console for details.</p>
                                    <button onclick="location.reload()" style="margin-top: 10px; padding: 8px 16px; background: #667eea; color: white; border: none; border-radius: 4px; cursor: pointer;">Refresh Page</button>
                                </div>
                            `;
                        }
                    }
                }, 200);
            } catch (error) {
                console.error('Error in initialization setup:', error);
                const viewer = document.getElementById('viewer');
                if (viewer) {
                    viewer.innerHTML = `
                        <div class="placeholder">
                            <div class="placeholder-icon">⚠️</div>
                            <p>Error initializing 3D viewer: ${error.message}</p>
                            <button onclick="location.reload()" style="margin-top: 10px; padding: 8px 16px; background: #667eea; color: white; border: none; border-radius: 4px; cursor: pointer;">Refresh Page</button>
                        </div>
                    `;
                }
            }
        } else if (checkCount >= maxChecks) {
            clearInterval(checkCesium);
            console.error('Cesium failed to load after 10 seconds');
            const viewer = document.getElementById('viewer');
            if (viewer) {
                viewer.innerHTML = `
                    <div class="placeholder">
                        <div class="placeholder-icon">⚠️</div>
                        <p><strong>Cesium library failed to load.</strong></p>
                        <p style="font-size: 14px; margin-top: 10px;">Please check:</p>
                        <ul style="text-align: left; margin: 10px 0; font-size: 12px;">
                            <li>Internet connection</li>
                            <li>Cesium CDN accessibility</li>
                            <li>Browser console (F12) for errors</li>
                            <li>Ad blockers or firewall settings</li>
                        </ul>
                        <button onclick="location.reload()" style="margin-top: 10px; padding: 8px 16px; background: #667eea; color: white; border: none; border-radius: 4px; cursor: pointer;">Refresh Page</button>
                    </div>
                `;
            }
        }
    }, 100);
});

