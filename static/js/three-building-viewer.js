// Three.js Building Viewer - Lightweight viewer for single buildings
class ThreeBuildingViewer {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        this.buildingMesh = null;
        this.testCube = null; // Test cube to verify rendering
        this.isInitialized = false;
        
        if (!this.container) {
            console.error('Three.js viewer container not found:', containerId);
            return;
        }
        
        this.init();
    }
    
    init() {
        try {
            if (typeof THREE === 'undefined') {
                throw new Error('Three.js library not loaded');
            }
            
            console.log('Initializing Three.js viewer, container:', this.container);
            console.log('Container dimensions:', this.container.clientWidth, 'x', this.container.clientHeight);
            
            // Scene
            this.scene = new THREE.Scene();
            this.scene.background = new THREE.Color(0xffffff);
            
            // Camera - ensure container has dimensions
            let width = this.container.clientWidth;
            let height = this.container.clientHeight;
            
            // If container has no dimensions, use CSS dimensions or defaults
            if (width === 0 || height === 0) {
                const computedStyle = window.getComputedStyle(this.container);
                width = parseInt(computedStyle.width) || 400;
                height = parseInt(computedStyle.height) || 400;
                console.warn('Container had 0 dimensions, using computed/default:', width, 'x', height);
            }
            
            console.log('Creating camera with dimensions:', width, 'x', height);
            
            this.camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 10000);
            this.camera.position.set(0, 0, 150);
            
            // Renderer
            this.renderer = new THREE.WebGLRenderer({ 
                antialias: true,
                alpha: false // Opaque background
            });
            this.renderer.setSize(width, height);
            this.renderer.setPixelRatio(window.devicePixelRatio);
            this.renderer.setClearColor(0xffffff, 1); // White background
            
            // Clear container first
            this.container.innerHTML = '';
            this.container.appendChild(this.renderer.domElement);
            
            // Ensure canvas is visible
            this.renderer.domElement.style.display = 'block';
            this.renderer.domElement.style.width = '100%';
            this.renderer.domElement.style.height = '100%';
            
            console.log('Three.js renderer created and added to container');
            console.log('Canvas element:', this.renderer.domElement);
            console.log('Canvas dimensions:', this.renderer.domElement.width, 'x', this.renderer.domElement.height);
            
            // Controls - OrbitControls will be available after script loads
            // We'll set it up after a small delay or make it optional
            setTimeout(() => {
                try {
                    if (typeof THREE !== 'undefined' && THREE.OrbitControls) {
                        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
                        this.controls.enableDamping = true;
                        this.controls.dampingFactor = 0.05;
                        this.controls.minDistance = 10;
                        this.controls.maxDistance = 500;
                    } else if (typeof OrbitControls !== 'undefined') {
                        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
                        this.controls.enableDamping = true;
                        this.controls.dampingFactor = 0.05;
                        this.controls.minDistance = 10;
                        this.controls.maxDistance = 500;
                    }
                } catch (controlsError) {
                    console.warn('OrbitControls not available, camera will be fixed:', controlsError);
                    // Camera can still be positioned manually
                }
            }, 100);
            
            // Lighting
            const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
            this.scene.add(ambientLight);
            
            const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
            directionalLight.position.set(50, 50, 50);
            this.scene.add(directionalLight);
            
            // Grid helper (optional, for reference) - make it smaller and less prominent
            const gridHelper = new THREE.GridHelper(50, 10, 0xcccccc, 0xeeeeee);
            gridHelper.position.y = -0.1; // Slightly below ground
            this.scene.add(gridHelper);
            
            // Add axes helper for debugging (small)
            const axesHelper = new THREE.AxesHelper(10);
            this.scene.add(axesHelper);
            
            // Add a test cube to verify rendering works (will be removed when building loads)
            const testGeometry = new THREE.BoxGeometry(5, 5, 5);
            const testMaterial = new THREE.MeshStandardMaterial({ color: 0x00ff00 });
            const testCube = new THREE.Mesh(testGeometry, testMaterial);
            testCube.position.set(0, 2.5, 0);
            this.scene.add(testCube);
            this.testCube = testCube; // Store reference to remove later
            
            // Render once immediately to show white background and test cube
            this.renderer.render(this.scene, this.camera);
            
            // Animation loop
            this.animate();
            
            this.isInitialized = true;
            console.log('Three.js building viewer initialized successfully');
            console.log('Scene children:', this.scene.children.length);
            console.log('Renderer canvas:', this.renderer.domElement);
            console.log('Canvas visible:', this.renderer.domElement.offsetWidth > 0 && this.renderer.domElement.offsetHeight > 0);
        } catch (error) {
            console.error('Error initializing Three.js viewer:', error);
            if (this.container) {
                this.container.innerHTML = `<div style="padding: 20px; text-align: center; color: #dc3545;">Error: ${error.message}</div>`;
            }
        }
    }
    
    animate() {
        requestAnimationFrame(() => this.animate());
        
        if (this.controls) {
            this.controls.update();
        }
        
        if (this.renderer && this.scene && this.camera) {
            this.renderer.render(this.scene, this.camera);
        }
    }
    
    loadBuilding(cityJSON) {
        if (!this.isInitialized) {
            console.error('Three.js viewer not initialized');
            return;
        }
        
        console.log('Loading building in Three.js viewer, CityJSON keys:', Object.keys(cityJSON));
        console.log('Full CityJSON:', JSON.stringify(cityJSON, null, 2));
        
        // Clear existing building
        if (this.buildingMesh) {
            this.scene.remove(this.buildingMesh);
            if (this.buildingMesh.geometry) {
                this.buildingMesh.geometry.dispose();
            }
            if (this.buildingMesh.material) {
                this.buildingMesh.material.dispose();
            }
            this.buildingMesh = null;
        }
        
        let buildingLoaded = false;
        
        try {
            const cityObjects = cityJSON.CityObjects || {};
            const vertices = cityJSON.vertices || [];
            const transform = cityJSON.transform || null;
            
            console.log('CityObjects count:', Object.keys(cityObjects).length);
            console.log('Vertices count:', vertices.length);
            console.log('Transform:', transform);
            
            if (Object.keys(cityObjects).length === 0) {
                console.error('No city objects in CityJSON');
                throw new Error('No city objects in CityJSON');
            }
            
            const objectId = Object.keys(cityObjects)[0];
            const cityObject = cityObjects[objectId];
            const geometries = cityObject.geometry || [];
            
            console.log('Building ID:', objectId);
            console.log('Geometries count:', geometries.length);
            
            if (geometries.length === 0) {
                console.error('No geometries in city object');
                throw new Error('No geometries in city object');
            }
            
            // Process first geometry
            const geometry = geometries[0];
            console.log('Processing geometry type:', geometry.type);
            console.log('Geometry data:', JSON.stringify(geometry, null, 2));
            
            const buildingShape = this.extractBuildingShape(geometry, vertices, transform);
            
            console.log('Extracted building shape points:', buildingShape.length);
            console.log('First few shape points:', buildingShape.slice(0, 3));
            
            if (!buildingShape || buildingShape.length < 3) {
                console.error('Invalid building shape, points:', buildingShape ? buildingShape.length : 0);
                console.error('Geometry type:', geometry.type);
                console.error('Geometry boundaries:', geometry.boundaries ? 'exists' : 'missing');
                console.error('Vertices count:', vertices.length);
                console.error('Transform:', transform);
                
                // Fallback: create a simple box building
                console.warn('Creating fallback box building due to geometry extraction failure');
                // BoxGeometry(width, height, depth) - height is Y-axis in Three.js
                const fallbackGeometry = new THREE.BoxGeometry(10, 20, 10); // width, height, depth
                const fallbackMaterial = new THREE.MeshStandardMaterial({
                    color: 0xff0000, // Red to indicate fallback
                    metalness: 0.1,
                    roughness: 0.8
                });
                this.buildingMesh = new THREE.Mesh(fallbackGeometry, fallbackMaterial);
                this.buildingMesh.position.set(0, 10, 0); // Y=10 means 10 units up (center of 20-unit tall building)
                this.scene.add(this.buildingMesh);
                
                // Fit camera to fallback building (isometric view)
                const center = new THREE.Vector3(0, 10, 0);
                const distance = 40;
                const angle = Math.PI / 6; // 30 degrees
                this.camera.position.set(
                    distance * Math.cos(angle) * Math.cos(angle),
                    center.y + distance * Math.sin(angle),
                    distance * Math.cos(angle) * Math.sin(angle)
                );
                this.camera.lookAt(center);
                this.camera.updateProjectionMatrix();
                if (this.controls) {
                    this.controls.target.copy(center);
                    this.controls.update();
                }
                this.renderer.render(this.scene, this.camera);
                
                buildingLoaded = true;
                console.log('Fallback building created and rendered');
                return; // Exit early, don't try to create normal building
            }
            
            // Calculate height
            const height = this.calculateBuildingHeight(geometry, vertices, transform);
            console.log('Building height:', height);
            
            // Calculate center and scale
            let centerX = 0, centerY = 0;
            let minX = Infinity, maxX = -Infinity;
            let minY = Infinity, maxY = -Infinity;
            
            buildingShape.forEach(p => {
                centerX += p.x;
                centerY += p.y;
                minX = Math.min(minX, p.x);
                maxX = Math.max(maxX, p.x);
                minY = Math.min(minY, p.y);
                maxY = Math.max(maxY, p.y);
            });
            centerX /= buildingShape.length;
            centerY /= buildingShape.length;
            
            const sizeX = maxX - minX;
            const sizeY = maxY - minY;
            const maxSize = Math.max(sizeX, sizeY);
            
            console.log('Shape center:', centerX, centerY);
            console.log('Shape size:', sizeX, 'x', sizeY, 'max:', maxSize);
            
            // If coordinates are very large (geographic coordinates), we need to scale them down
            // For Three.js, we want coordinates in a reasonable range (e.g., -50 to 50)
            let scale = 1;
            if (maxSize > 100) {
                // Coordinates are likely in meters from a geographic origin - scale down
                // Scale to fit in ~50 unit range (so building is visible)
                scale = 50 / maxSize;
                console.log('Scaling coordinates by factor:', scale, '(maxSize was', maxSize, ')');
            } else if (maxSize < 0.1) {
                // Coordinates are too small, scale up
                scale = 50 / maxSize;
                console.log('Scaling coordinates UP by factor:', scale, '(maxSize was', maxSize, ')');
            } else {
                console.log('No scaling needed, maxSize:', maxSize);
            }
            
            // Create 3D shape (centered and scaled)
            const shape = new THREE.Shape();
            const firstPoint = buildingShape[0];
            shape.moveTo((firstPoint.x - centerX) * scale, (firstPoint.y - centerY) * scale);
            
            for (let i = 1; i < buildingShape.length; i++) {
                const point = buildingShape[i];
                shape.lineTo((point.x - centerX) * scale, (point.y - centerY) * scale);
            }
            shape.lineTo((firstPoint.x - centerX) * scale, (firstPoint.y - centerY) * scale); // Close the shape
            
            console.log('Shape created with', buildingShape.length, 'points, scaled by', scale);
            
            // Scale height proportionally if we scaled the footprint
            const scaledHeight = height * scale;
            
            // Extrude
            const extrudeSettings = {
                depth: scaledHeight,
                bevelEnabled: false
            };
            
            const extrudeGeometry = new THREE.ExtrudeGeometry(shape, extrudeSettings);
            
            // Center the geometry (move to origin)
            extrudeGeometry.translate(0, 0, -scaledHeight / 2);
            
            console.log('Extruded geometry created, height:', scaledHeight);
            
            // Material (yellow highlight)
            const material = new THREE.MeshStandardMaterial({
                color: 0xffeb3b, // Yellow
                metalness: 0.1,
                roughness: 0.8
            });
            
            // Create mesh
            this.buildingMesh = new THREE.Mesh(extrudeGeometry, material);
            this.buildingMesh.visible = true; // Ensure it's visible
            
            // Rotate building to stand upright (Three.js uses Y-up, but we extruded along Z)
            // Rotate 90 degrees around X-axis to make Z become Y
            this.buildingMesh.rotation.x = -Math.PI / 2;
            
            this.scene.add(this.buildingMesh);
            
            // Calculate bounding box BEFORE camera fitting
            const boundingBox = new THREE.Box3().setFromObject(this.buildingMesh);
            const boxCenter = boundingBox.getCenter(new THREE.Vector3());
            const boxSize = boundingBox.getSize(new THREE.Vector3());
            
            console.log('Building mesh added to scene');
            console.log('Building mesh position:', this.buildingMesh.position);
            console.log('Building bounding box center:', boxCenter);
            console.log('Building bounding box size:', boxSize);
            console.log('Building scaled height:', scaledHeight);
            console.log('Building footprint size:', maxSize, 'scaled:', maxSize * scale);
            
            // Check if building has valid dimensions
            const maxDim = Math.max(boxSize.x, boxSize.y, boxSize.z);
            if (maxDim < 0.001 || !isFinite(maxDim)) {
                throw new Error(`Invalid building dimensions: ${boxSize.x} x ${boxSize.y} x ${boxSize.z}`);
            }
            
            // Force a render immediately (before camera adjustment)
            this.renderer.render(this.scene, this.camera);
            
            // Fit camera to building
            this.fitCameraToBuilding();
            
            // Verify camera can see the building
            const cameraDistance = this.camera.position.distanceTo(boxCenter);
            console.log('Camera distance from building center:', cameraDistance);
            console.log('Building max dimension:', maxDim);
            console.log('Camera should see building:', cameraDistance < maxDim * 10);
            
            // Render again after camera adjustment
            this.renderer.render(this.scene, this.camera);
            
            // Verify building is actually visible by checking if it's in camera frustum
            const frustum = new THREE.Frustum();
            frustum.setFromProjectionMatrix(
                new THREE.Matrix4().multiplyMatrices(
                    this.camera.projectionMatrix,
                    this.camera.matrixWorldInverse
                )
            );
            const buildingInFrustum = frustum.intersectsBox(boundingBox);
            console.log('Building in camera frustum:', buildingInFrustum);
            
            if (!buildingInFrustum) {
                console.warn('Building is not in camera frustum! Adjusting camera...');
                // Try to fix camera position
                this.camera.position.set(boxCenter.x, boxCenter.y + maxDim * 2, boxCenter.z + maxDim * 2);
                this.camera.lookAt(boxCenter);
                this.camera.updateProjectionMatrix();
                if (this.controls) {
                    this.controls.target.copy(boxCenter);
                    this.controls.update();
                }
                this.renderer.render(this.scene, this.camera);
            }
            
            buildingLoaded = true;
            
            // DON'T remove test cube yet - keep it visible alongside building for debugging
            // Only remove if building is confirmed visible
            setTimeout(() => {
                if (this.buildingMesh && this.buildingMesh.visible && this.testCube) {
                    console.log('Building confirmed visible, removing test cube');
                    this.scene.remove(this.testCube);
                    this.testCube.geometry.dispose();
                    this.testCube.material.dispose();
                    this.testCube = null;
                    this.renderer.render(this.scene, this.camera);
                } else {
                    console.warn('Building not confirmed visible, keeping test cube');
                }
            }, 500);
            
            console.log('Building loaded successfully in Three.js viewer');
            console.log('Scene now has', this.scene.children.length, 'children');
            console.log('Building mesh visible:', this.buildingMesh.visible);
            console.log('Camera position:', this.camera.position);
            console.log('Camera looking at:', this.camera.getWorldDirection(new THREE.Vector3()));
            
            // Force multiple renders to ensure visibility
            for (let i = 0; i < 3; i++) {
                setTimeout(() => {
                    this.renderer.render(this.scene, this.camera);
                }, i * 100);
            }
        } catch (error) {
            console.error('Error loading building in Three.js viewer:', error);
            console.error('Error stack:', error.stack);
            console.error('Error details:', {
                message: error.message,
                name: error.name,
                geometry: geometry ? { type: geometry.type, hasBoundaries: !!geometry.boundaries } : null,
                verticesCount: vertices ? vertices.length : 0,
                transform: transform
            });
            
            // Keep test cube visible if building failed to load (change to red to indicate error)
            if (this.testCube) {
                console.log('Building load failed, changing test cube to red');
                this.testCube.material.color.setHex(0xff0000); // Red for error
                this.renderer.render(this.scene, this.camera);
            } else {
                console.log('Building load failed, creating red test cube');
                const testGeometry = new THREE.BoxGeometry(5, 5, 5);
                const testMaterial = new THREE.MeshStandardMaterial({ color: 0xff0000 }); // Red for error
                this.testCube = new THREE.Mesh(testGeometry, testMaterial);
                this.testCube.position.set(0, 2.5, 0);
                this.scene.add(this.testCube);
                this.renderer.render(this.scene, this.camera);
            }
        }
    }
    
    extractBuildingShape(geometry, vertices, transform) {
        const points = [];
        
        try {
            console.log('Extracting building shape, geometry type:', geometry.type);
            console.log('Geometry structure:', JSON.stringify(geometry, null, 2));
            
            // Pre-transform vertices if transform is available (same as Cesium viewer)
            let transformedVertices = vertices;
            if (transform && vertices.length > 0) {
                transformedVertices = vertices.map(vertex => [
                    vertex[0] * transform.scale[0] + transform.translate[0],
                    vertex[1] * transform.scale[1] + transform.translate[1],
                    vertex[2] * transform.scale[2] + transform.translate[2]
                ]);
                console.log('Pre-transformed', vertices.length, 'vertices');
            }
            
            if (geometry.type === 'Solid' && geometry.boundaries) {
                // Get the outer shell (first boundary) - same logic as Cesium
                const outerShell = geometry.boundaries[0];
                
                if (outerShell && outerShell.length > 0) {
                    // Get the first face's first ring (footprint) - same as Cesium
                    const firstFace = outerShell[0];
                    if (firstFace && firstFace.length > 0) {
                        const firstRing = firstFace[0];
                        
                        console.log('Solid geometry: outerShell faces:', outerShell.length, 'firstFace rings:', firstFace.length, 'firstRing vertices:', firstRing.length);
                        
                        firstRing.forEach(vertexIdx => {
                            if (vertexIdx >= 0 && vertexIdx < transformedVertices.length) {
                                const vertex = transformedVertices[vertexIdx];
                                points.push({ x: vertex[0], y: vertex[1], z: vertex[2] });
                            } else {
                                console.warn('Invalid vertex index:', vertexIdx, 'vertices length:', transformedVertices.length);
                            }
                        });
                    } else {
                        console.warn('Solid: firstFace is empty or invalid');
                    }
                } else {
                    console.warn('Solid: outerShell is empty or invalid');
                }
            } else if (geometry.type === 'MultiSurface' && geometry.boundaries) {
                // Handle MultiSurface - use first surface (same as Cesium)
                if (geometry.boundaries.length > 0) {
                    const firstSurface = geometry.boundaries[0];
                    if (firstSurface && firstSurface.length > 0) {
                        const firstRing = firstSurface[0];
                        
                        console.log('MultiSurface: surfaces:', geometry.boundaries.length, 'firstSurface rings:', firstSurface.length, 'firstRing vertices:', firstRing.length);
                        
                        firstRing.forEach(vertexIdx => {
                            if (vertexIdx >= 0 && vertexIdx < transformedVertices.length) {
                                const vertex = transformedVertices[vertexIdx];
                                points.push({ x: vertex[0], y: vertex[1], z: vertex[2] });
                            } else {
                                console.warn('Invalid vertex index:', vertexIdx, 'vertices length:', transformedVertices.length);
                            }
                        });
                    } else {
                        console.warn('MultiSurface: firstSurface is empty or invalid');
                    }
                } else {
                    console.warn('MultiSurface: boundaries array is empty');
                }
            } else if (geometry.type === 'CompositeSurface' && geometry.boundaries) {
                // Handle CompositeSurface - try first surface
                if (geometry.boundaries.length > 0) {
                    const firstSurface = geometry.boundaries[0];
                    if (firstSurface && firstSurface.length > 0) {
                        const firstRing = firstSurface[0];
                        console.log('CompositeSurface: surfaces:', geometry.boundaries.length, 'firstRing vertices:', firstRing.length);
                        
                        firstRing.forEach(vertexIdx => {
                            if (vertexIdx >= 0 && vertexIdx < transformedVertices.length) {
                                const vertex = transformedVertices[vertexIdx];
                                points.push({ x: vertex[0], y: vertex[1], z: vertex[2] });
                            }
                        });
                    }
                }
            } else {
                console.warn('Unsupported geometry type:', geometry.type, 'or missing boundaries');
                console.log('Available geometry keys:', Object.keys(geometry));
            }
            
            console.log('Extracted', points.length, 'points for building shape');
            
            // Remove duplicate consecutive points
            const uniquePoints = [];
            for (let i = 0; i < points.length; i++) {
                const prev = uniquePoints[uniquePoints.length - 1];
                const curr = points[i];
                if (!prev || prev.x !== curr.x || prev.y !== curr.y) {
                    uniquePoints.push(curr);
                }
            }
            
            if (uniquePoints.length < points.length) {
                console.log('Removed', points.length - uniquePoints.length, 'duplicate points');
            }
            
            return uniquePoints.length >= 3 ? uniquePoints : points; // Return at least original points if uniquePoints is too small
        } catch (error) {
            console.error('Error extracting building shape:', error);
            console.error('Error stack:', error.stack);
            return points; // Return whatever we have
        }
    }
    
    calculateBuildingHeight(geometry, vertices, transform) {
        let minZ = Infinity;
        let maxZ = -Infinity;
        
        try {
            // Pre-transform vertices if transform is available (same as Cesium)
            let transformedVertices = vertices;
            if (transform && vertices.length > 0) {
                transformedVertices = vertices.map(vertex => [
                    vertex[0] * transform.scale[0] + transform.translate[0],
                    vertex[1] * transform.scale[1] + transform.translate[1],
                    vertex[2] * transform.scale[2] + transform.translate[2]
                ]);
            }
            
            const processVertex = (vertex) => {
                const z = vertex[2]; // Already transformed if needed
                minZ = Math.min(minZ, z);
                maxZ = Math.max(maxZ, z);
            };
            
            if (geometry.type === 'Solid' && geometry.boundaries) {
                const outerShell = geometry.boundaries[0];
                if (outerShell) {
                    outerShell.forEach(face => {
                        if (face) {
                            face.forEach(ring => {
                                if (ring) {
                                    ring.forEach(vertexIdx => {
                                        if (vertexIdx >= 0 && vertexIdx < transformedVertices.length) {
                                            processVertex(transformedVertices[vertexIdx]);
                                        }
                                    });
                                }
                            });
                        }
                    });
                }
            } else if (geometry.type === 'MultiSurface' && geometry.boundaries) {
                geometry.boundaries.forEach(surface => {
                    if (surface) {
                        surface.forEach(ring => {
                            if (ring) {
                                ring.forEach(vertexIdx => {
                                    if (vertexIdx >= 0 && vertexIdx < transformedVertices.length) {
                                        processVertex(transformedVertices[vertexIdx]);
                                    }
                                });
                            }
                        });
                    }
                });
            } else if (geometry.type === 'CompositeSurface' && geometry.boundaries) {
                geometry.boundaries.forEach(surface => {
                    if (surface) {
                        surface.forEach(ring => {
                            if (ring) {
                                ring.forEach(vertexIdx => {
                                    if (vertexIdx >= 0 && vertexIdx < transformedVertices.length) {
                                        processVertex(transformedVertices[vertexIdx]);
                                    }
                                });
                            }
                        });
                    }
                });
            }
            
            const height = maxZ > minZ ? (maxZ - minZ) : 10;
            console.log('Calculated building height:', height, '(minZ:', minZ, 'maxZ:', maxZ, ')');
            return height;
        } catch (error) {
            console.error('Error calculating building height:', error);
            console.error('Error stack:', error.stack);
            return 10; // Default height if calculation fails
        }
    }
    
    fitCameraToBuilding() {
        if (!this.buildingMesh) {
            console.warn('No building mesh to fit camera to');
            return;
        }
        
        try {
            const box = new THREE.Box3().setFromObject(this.buildingMesh);
            const center = box.getCenter(new THREE.Vector3());
            const size = box.getSize(new THREE.Vector3());
            
            console.log('Building bounding box center:', center);
            console.log('Building bounding box size:', size);
            
            const maxDim = Math.max(size.x, size.y, size.z);
            // Use a consistent distance for all buildings (isometric-like view)
            const distance = Math.max(maxDim * 2.5, 100);
            
            console.log('Camera distance:', distance);
            
            // Position camera at an isometric angle (top-front-right view)
            // This gives a nice 3D view of the building
            const angle = Math.PI / 6; // 30 degrees
            this.camera.position.set(
                center.x + distance * Math.cos(angle) * Math.cos(angle),
                center.y + distance * Math.sin(angle), // Higher up for top-down-ish view
                center.z + distance * Math.cos(angle) * Math.sin(angle)
            );
            this.camera.lookAt(center);
            this.camera.updateProjectionMatrix();
            
            if (this.controls) {
                this.controls.target.copy(center);
                this.controls.update();
            }
            
            console.log('Camera positioned at:', this.camera.position);
            console.log('Camera looking at:', center);
        } catch (error) {
            console.error('Error fitting camera to building:', error);
        }
    }
    
    resize() {
        if (!this.renderer || !this.camera || !this.container) return;
        
        const width = this.container.clientWidth;
        const height = this.container.clientHeight;
        
        this.camera.aspect = width / height;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(width, height);
    }
    
    dispose() {
        if (this.buildingMesh) {
            this.scene.remove(this.buildingMesh);
            if (this.buildingMesh.geometry) this.buildingMesh.geometry.dispose();
            if (this.buildingMesh.material) this.buildingMesh.material.dispose();
        }
        
        if (this.renderer) {
            this.renderer.dispose();
        }
    }
}
