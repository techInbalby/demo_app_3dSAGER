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
        
        // The Hague coordinates (default location)
        // NOTE: CityJSON files may use local coordinate systems
        // Adjust these values based on your actual coordinate reference system (CRS)
        // If your CityJSON uses a local CRS (e.g., RD New EPSG:28992), you'll need proper transformation
        // For now, this assumes coordinates are relative to The Hague center
        this.defaultLocation = {
            longitude: 4.3007,  // The Hague longitude (WGS84)
            latitude: 52.0705,   // The Hague latitude (WGS84)
            height: 5000,       // Initial camera height
            // Coordinate system origin (if using local coordinates)
            originX: 0,         // Adjust if your CityJSON has a known origin
            originY: 0,         // Adjust if your CityJSON has a known origin
            // Scale factor for coordinate conversion (meters per degree)
            // Approximate: 1 degree latitude ≈ 111,320 meters
            metersPerDegree: 111320.0
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
            infoBox: true, // Enable info box for clicked buildings
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
        
        // Select the entity (shows info box)
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
        
        // Get matches for this building (call your API)
        this.loadBuildingMatches(buildingId, cityObject);
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
        fetch(apiUrl)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                this.parseCityJSON(data);
            })
            .catch(error => {
                console.error('Error loading CityJSON:', error);
                this.showError('Failed to load CityJSON: ' + error.message);
            });
    }
    
    parseCityJSON(cityJSON) {
        try {
            // Store city objects
            this.cityObjects = cityJSON.CityObjects || {};
            const vertices = cityJSON.vertices || [];
            const transform = cityJSON.transform || null;
            
            // Calculate bounding box for camera fitting
            this.calculateBoundingBox(vertices, transform);
            
            // Process each city object
            let entityCount = 0;
            Object.keys(this.cityObjects).forEach(objectId => {
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
            });
            
            // Fit camera to all buildings
            if (entityCount > 0) {
                this.fitCameraToBuildings();
            }
            
            this.hideLoading();
            console.log(`Loaded ${Object.keys(this.cityObjects).length} city objects, ${entityCount} entities`);
            
        } catch (error) {
            console.error('Error parsing CityJSON:', error);
            this.showError('Failed to parse CityJSON: ' + error.message);
        }
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
        const entity = this.viewer.entities.add({
            id: `building_${objectId}_${Date.now()}`,
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
                                
                                // Convert local coordinates to geodetic
                                // Use baseHeight (minimum Z) for footprint, not the vertex Z
                                const metersPerDegree = this.defaultLocation.metersPerDegree;
                                const lon = this.defaultLocation.longitude + 
                                    ((vertex[0] - this.defaultLocation.originX) / metersPerDegree);
                                const lat = this.defaultLocation.latitude + 
                                    ((vertex[1] - this.defaultLocation.originY) / metersPerDegree);
                                
                                // Use baseHeight for footprint (ground level)
                                positions.push(Cesium.Cartesian3.fromDegrees(lon, lat, baseHeight));
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
                                
                                const metersPerDegree = this.defaultLocation.metersPerDegree;
                                const lon = this.defaultLocation.longitude + 
                                    ((vertex[0] - this.defaultLocation.originX) / metersPerDegree);
                                const lat = this.defaultLocation.latitude + 
                                    ((vertex[1] - this.defaultLocation.originY) / metersPerDegree);
                                
                                // Use baseHeight for footprint (ground level)
                                positions.push(Cesium.Cartesian3.fromDegrees(lon, lat, baseHeight));
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
                const lon = this.defaultLocation.longitude + (center.x / 111320.0);
                const lat = this.defaultLocation.latitude + (center.y / 111320.0);
                const height = Math.max(
                    Math.abs(this.boundingBox.max.x - this.boundingBox.min.x),
                    Math.abs(this.boundingBox.max.y - this.boundingBox.min.y)
                ) * 1.5;
                
                this.viewer.camera.flyTo({
                    destination: Cesium.Cartesian3.fromDegrees(lon, lat, height),
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
    }
    
    clearPlaceholder() {
        const placeholder = this.container.querySelector('.placeholder');
        if (placeholder) {
            placeholder.remove();
        }
    }
    
    showLoading() {
        // Cesium handles loading internally, but we can add a custom message
        console.log('Loading CityJSON...');
    }
    
    hideLoading() {
        console.log('CityJSON loaded');
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

