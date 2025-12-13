// Cesium CityJSON Viewer with Clickable Buildings
// Converts CityJSON to Cesium entities with geospatial positioning

class CesiumCityJSONViewer {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.viewer = null;
        this.cityObjects = {};
        this.buildingEntities = new Map(); // Store building entities for click handling
        this.isInitialized = false;
        this.boundingBox = null; // Store bounding box for camera fitting
        this.crs = null; // Store coordinate reference system from metadata
        this.sourceCRS = null; // Source CRS from CityJSON metadata
        
        // The Hague coordinates (default location)
        this.defaultLocation = {
            longitude: 4.3007,  // The Hague longitude (WGS84)
            latitude: 52.0705,   // The Hague latitude (WGS84)
            height: 5000,       // Initial camera height
        };
        
        this.init();
    }
    
    init() {
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
            const osmImagery = new Cesium.OpenStreetMapImageryProvider({
                url: 'https://a.tile.openstreetmap.org/'
            });
            
            this.viewer = new Cesium.Viewer(this.container, {
            terrainProvider: new Cesium.EllipsoidTerrainProvider(), // Simple ellipsoid - no token needed
            imageryProvider: osmImagery, // Use OpenStreetMap instead of Ion imagery
            baseLayerPicker: false, // Disable to avoid Ion token requirement
            vrButton: false,
            geocoder: false, // Geocoder may also use Ion, disable if not needed
            homeButton: true,
            sceneModePicker: true,
            navigationHelpButton: true,
            animation: false,
            timeline: false,
            fullscreenButton: true,
            infoBox: false, // Disable Cesium info box - using custom window instead
            selectionIndicator: true // Show selection indicator
            });
            
            // Set background to white
            this.viewer.scene.backgroundColor = Cesium.Color.WHITE;
            this.viewer.scene.globe.baseColor = Cesium.Color.WHITE;
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
            
            // Setup click handler for buildings
            this.setupClickHandler();
            
            // Remove placeholder if exists
            this.clearPlaceholder();
            
            this.isInitialized = true;
            console.log('Cesium CityJSON Viewer initialized successfully');
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
                // Building was clicked
                const entity = pickedObject.id;
                const buildingId = entity.buildingId;
                
                if (buildingId && this.cityObjects[buildingId]) {
                    this.onBuildingClicked(buildingId, entity);
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
                this.viewer.canvas.style.cursor = 'pointer';
            } else {
                this.viewer.canvas.style.cursor = 'default';
            }
        }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
    }
    
    onBuildingClicked(buildingId, entity) {
        const cityObject = this.cityObjects[buildingId];
        
        // Select the entity (for highlighting)
        this.viewer.selectedEntity = entity;
        
        // Highlight the building temporarily
        const originalMaterial = entity.polygon.material;
        entity.polygon.material = Cesium.Color.YELLOW.withAlpha(0.7);
        
        // Reset highlight after 2 seconds
        setTimeout(() => {
            if (entity.polygon && originalMaterial) {
                entity.polygon.material = originalMaterial;
            }
        }, 2000);
        
        // Show custom building properties window instead of Cesium info box
        if (window.showBuildingProperties) {
            window.showBuildingProperties(buildingId, cityObject);
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
    
    loadCityJSON(filePath) {
        if (!this.isInitialized) {
            console.error('Viewer not initialized');
            return;
        }
        
        console.log('Loading CityJSON file:', filePath);
        
        // Clear existing buildings
        this.clearBuildings();
        
        // Show loading
        this.showLoading();
        
        // Fetch CityJSON
        const apiUrl = `/api/data/file/${encodeURIComponent(filePath)}`;
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
    
    parseCityJSON(cityJSON) {
        const parseStartTime = performance.now();
        try {
            // Store city objects
            this.cityObjects = cityJSON.CityObjects || {};
            const vertices = cityJSON.vertices || [];
            const transform = cityJSON.transform || null;
            
            console.log(`Parsing CityJSON: ${Object.keys(this.cityObjects).length} objects, ${vertices.length} vertices`);
            
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
            const objectIds = Object.keys(this.cityObjects);
            const totalObjects = objectIds.length;
            let entityCount = 0;
            let processedCount = 0;
            const batchSize = 50; // Process 50 buildings at a time
            
            this.updateLoadingProgress(`Processing ${totalObjects} city objects...`);
            
            const processBatch = (startIndex) => {
                const endIndex = Math.min(startIndex + batchSize, totalObjects);
                
                for (let i = startIndex; i < endIndex; i++) {
                    const objectId = objectIds[i];
                    const cityObject = this.cityObjects[objectId];
                    const geometries = cityObject.geometry || [];
                    
                    geometries.forEach(geometry => {
                        const entity = this.createBuildingEntity(
                            objectId,
                            cityObject,
                            geometry,
                            vertices,
                            transform
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
                    // Use setTimeout to allow UI to update
                    setTimeout(() => processBatch(endIndex), 0);
                } else {
                    // All done
                    const totalTime = performance.now() - parseStartTime;
                    console.log(`Loaded ${totalObjects} city objects, ${entityCount} entities in ${(totalTime / 1000).toFixed(2)}s`);
                    this.updateLoadingProgress('Finalizing...');
                    
                    // Fit camera to all buildings
                    if (entityCount > 0) {
                        this.fitCameraToBuildings();
                    }
                    
                    // Small delay before hiding loading to show completion
                    setTimeout(() => {
                        this.hideLoading();
                    }, 500);
                }
            };
            
            // Start processing buildings in batches
            processBatch(0);
            
        } catch (error) {
            console.error('Error parsing CityJSON:', error);
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
                const epsgMatch = this.sourceCRS.match(/(\d+)/);
                if (epsgMatch) {
                    const epsgCode = epsgMatch[1];
                    sourceEPSG = `EPSG:${epsgCode}`;
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
                console.error('Proj4 transformation failed:', error);
                console.error('Source CRS:', this.sourceCRS, 'Coordinates:', { x, y });
                // Fallback to approximation
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
        // Calculate building's own bounding box from its geometry
        const buildingBbox = this.calculateBuildingBoundingBox(geometry, vertices, transform);
        
        if (!buildingBbox) {
            console.warn(`Skipping entity ${objectId}: could not calculate bounding box`);
            return null;
        }
        
        // Convert CityJSON geometry to Cesium positions (footprint at ground level)
        const positions = this.convertGeometryToPositions(geometry, vertices, transform, buildingBbox.min.z);
        
        if (!positions || positions.length < 3) {
            console.warn(`Skipping entity ${objectId}: insufficient positions`);
            return null;
        }
        
        // Get building height from its own bounding box
        const height = buildingBbox.max.z - buildingBbox.min.z;
        
        if (height <= 0) {
            console.warn(`Skipping entity ${objectId}: invalid height ${height}`);
            return null;
        }
        
        // Create Cesium entity
        // Let Cesium auto-generate unique IDs to avoid duplicate ID errors
        const entity = this.viewer.entities.add({
            name: cityObject.attributes?.name || objectId,
            buildingId: objectId,
            polygon: {
                hierarchy: positions,
                extrudedHeight: height,
                height: 0, // Base at ground level
                material: this.getMaterialForObjectType(cityObject.type),
                outline: true,
                outlineColor: Cesium.Color.BLACK.withAlpha(0.3),
                outlineWidth: 1
            },
            description: this.createBuildingDescription(cityObject)
        });
        
        // Store entity for click handling
        if (!this.buildingEntities.has(objectId)) {
            this.buildingEntities.set(objectId, []);
        }
        this.buildingEntities.get(objectId).push(entity);
        
        return entity;
    }
    
    calculateBuildingBoundingBox(geometry, vertices, transform) {
        // Calculate bounding box for this specific building's geometry
        let minX = Infinity, minY = Infinity, minZ = Infinity;
        let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
        
        try {
            const processVertex = (vertex) => {
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
                                let vertex = vertices[vertexIdx];
                                
                                // Apply transform if available
                                if (transform) {
                                    vertex = [
                                        vertex[0] * transform.scale[0] + transform.translate[0],
                                        vertex[1] * transform.scale[1] + transform.translate[1],
                                        vertex[2] * transform.scale[2] + transform.translate[2]
                                    ];
                                }
                                
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
                                let vertex = vertices[vertexIdx];
                                
                                if (transform) {
                                    vertex = [
                                        vertex[0] * transform.scale[0] + transform.translate[0],
                                        vertex[1] * transform.scale[1] + transform.translate[1],
                                        vertex[2] * transform.scale[2] + transform.translate[2]
                                    ];
                                }
                                
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
    
    getMaterialForObjectType(objectType) {
        // Colors with full opacity (255 = fully opaque, no transparency)
        const colors = {
            'Building': Cesium.Color.fromBytes(116, 151, 223, 255),
            'BuildingPart': Cesium.Color.fromBytes(116, 151, 223, 255),
            'BuildingInstallation': Cesium.Color.fromBytes(116, 151, 223, 255),
            'Bridge': Cesium.Color.fromBytes(153, 153, 153, 255),
            'BridgePart': Cesium.Color.fromBytes(153, 153, 153, 255),
            'Road': Cesium.Color.fromBytes(153, 153, 153, 255),
            'WaterBody': Cesium.Color.fromBytes(77, 166, 255, 255),
            'PlantCover': Cesium.Color.fromBytes(57, 172, 57, 255),
            'LandUse': Cesium.Color.fromBytes(255, 255, 179, 255)
        };
        
        return colors[objectType] || Cesium.Color.fromBytes(136, 136, 136, 255);
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
    
    fitCameraToBuildings() {
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
        });
    }
    
    clearBuildings() {
        this.buildingEntities.forEach(entityArray => {
            entityArray.forEach(entity => {
                this.viewer.entities.remove(entity);
            });
        });
        this.buildingEntities.clear();
        this.cityObjects = {};
        this.boundingBox = null;
        this.crs = null;
        this.sourceCRS = null;
    }
    
    clearPlaceholder() {
        const placeholder = this.container.querySelector('.placeholder');
        if (placeholder) {
            placeholder.remove();
        }
    }
    
    showLoading() {
        console.log('Loading CityJSON...');
        // Show visual loading indicator
        if (this.container) {
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
    
    resetCamera() {
        this.fitCameraToBuildings();
    }
    
    toggleFullscreen() {
        if (this.viewer && this.viewer.fullscreenButton) {
            this.viewer.fullscreenButton.viewModel.command();
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

