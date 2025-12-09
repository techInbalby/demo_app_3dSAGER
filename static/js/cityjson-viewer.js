// CityJSON 3D Viewer using custom parser
class CityJSONViewer {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        this.parser = null;
        this.isInitialized = false;
        
        this.init();
    }
    
    init() {
        if (typeof THREE === 'undefined') {
            console.error('Three.js not loaded');
            return;
        }
        
        // Custom parser will be used instead of external library
        
        // Create scene
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0xf0f8ff);
        
        // Create camera
        this.camera = new THREE.PerspectiveCamera(75, this.container.offsetWidth / this.container.offsetHeight, 0.1, 10000);
        this.camera.position.set(10, 10, 10);
        this.camera.lookAt(0, 0, 0);
        
        // Create renderer
        this.renderer = new THREE.WebGLRenderer({ antialias: true });
        this.renderer.setSize(this.container.offsetWidth, this.container.offsetHeight);
        this.renderer.shadowMap.enabled = true;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        
        // Add renderer to container
        this.container.innerHTML = '';
        this.container.appendChild(this.renderer.domElement);
        
        // Add camera properties display
        this.addCameraPropertiesDisplay();
        
        // Add navigation controls
        this.addNavigationControls();
        
        // Add controls
        if (typeof THREE.OrbitControls !== 'undefined') {
            this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
            this.controls.enableDamping = true;
            this.controls.dampingFactor = 0.05;
            this.controls.enableZoom = true;
            this.controls.enablePan = true;
            this.controls.enableRotate = true;
        }
        
        // Add lighting
        this.setupLighting();
        
        // Start render loop
        this.animate();
        
        this.isInitialized = true;
        console.log('CityJSON 3D Viewer initialized successfully');
    }
    
    setupLighting() {
        // Ambient light
        const ambientLight = new THREE.AmbientLight(0x404040, 0.6);
        this.scene.add(ambientLight);
        
        // Directional light
        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
        directionalLight.position.set(100, 100, 100);
        directionalLight.castShadow = true;
        directionalLight.shadow.mapSize.width = 2048;
        directionalLight.shadow.mapSize.height = 2048;
        directionalLight.shadow.camera.near = 0.5;
        directionalLight.shadow.camera.far = 500;
        directionalLight.shadow.camera.left = -100;
        directionalLight.shadow.camera.right = 100;
        directionalLight.shadow.camera.top = 100;
        directionalLight.shadow.camera.bottom = -100;
        this.scene.add(directionalLight);
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
    
    loadCityJSON(filePath) {
        if (!this.isInitialized) {
            console.error('Viewer not initialized');
            return;
        }
        
        console.log('Loading CityJSON file:', filePath);
        
        // Clear existing objects
        this.clearScene();
        
        // Show loading indicator
        this.showLoading();
        
        // Fetch the CityJSON file (filePath is already relative)
        const apiUrl = `/api/data/file/${encodeURIComponent(filePath)}`;
        console.log('Fetching from API:', apiUrl);
        fetch(apiUrl)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                console.log('CityJSON data loaded:', data);
                this.parseCityJSON(data);
            })
            .catch(error => {
                console.error('Error loading CityJSON:', error);
                this.showError('Failed to load CityJSON file: ' + error.message);
            });
    }
    
    parseCityJSON(cityJSON) {
        try {
            // Create custom parser
            this.parser = new CityJSONParser();
            this.parser.parse(cityJSON);
            
            // Create meshes for each city object
            this.createCityObjectMeshes();
            
            console.log('CityJSON parsed successfully');
            this.hideLoading();
            
            // Ensure camera properties display is available
            this.ensureCameraPropertiesDisplay();
            
            // Ensure navigation controls are available
            this.ensureNavigationControls();
            
            // Delay camera positioning to ensure meshes are fully created
            setTimeout(() => {
                this.fitCameraToModel();
            }, 100);
            
        } catch (error) {
            console.error('Error parsing CityJSON:', error);
            this.showError('Failed to parse CityJSON: ' + error.message);
        }
    }
    
    createCityObjectMeshes() {
        if (!this.parser) return;
        
        const cityObjects = this.parser.cityObjects;
        let meshCount = 0;
        
        Object.keys(cityObjects).forEach(objectId => {
            const cityObject = cityObjects[objectId];
            const geometries = cityObject.geometry || [];
            
            geometries.forEach(geometry => {
                const mesh = this.parser.createGeometry(objectId, geometry);
                if (mesh) {
                    this.scene.add(mesh);
                    meshCount++;
                }
            });
        });
        
        console.log(`Created ${meshCount} meshes from CityJSON`);
    }
    
    updateScene() {
        // Scene is updated in createCityObjectMeshes
        console.log('Scene updated with CityJSON data');
    }
    
    clearCityObjects() {
        // Remove all city objects from scene
        const objectsToRemove = [];
        this.scene.traverse((child) => {
            if (child.isMesh && child.userData.isCityObject) {
                objectsToRemove.push(child);
            }
        });
        
        objectsToRemove.forEach(obj => {
            if (obj.parent) {
                obj.parent.remove(obj);
            }
        });
    }
    
    clearScene() {
        if (!this.scene) return;
        
        // Remove all objects except lights
        const objectsToRemove = [];
        this.scene.traverse((child) => {
            if (child !== this.scene && 
                child.type !== 'AmbientLight' && 
                child.type !== 'DirectionalLight' &&
                !child.userData.isLight) {
                objectsToRemove.push(child);
            }
        });
        
        objectsToRemove.forEach(obj => {
            if (obj.parent) {
                obj.parent.remove(obj);
            }
        });
    }
    
    fitCameraToModel() {
        if (!this.parser || !this.parser.boundingBox) {
            console.log('No parser or bounding box available');
            return;
        }
        
        const bbox = this.parser.boundingBox;
        const center = bbox.getCenter(new THREE.Vector3());
        const size = bbox.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);
        
        console.log('Bounding box:', bbox);
        console.log('Center:', center);
        console.log('Size:', size);
        console.log('Max dimension:', maxDim);
        
        // Ensure we have a valid bounding box
        if (maxDim === 0) {
            console.log('Invalid bounding box - using default positioning');
            this.camera.position.set(50, 50, 50);
            this.camera.lookAt(0, 0, 0);
            return;
        }
        
        const distance = maxDim * 0.3; // Much closer to the model
        
        // Position camera for optimal aerial view 
        this.camera.position.set(
            center.x + distance * 0.05,  // To the side (positive X)
            center.y - distance * 1.35, // Lower height (negative Y)
            center.z + distance * 1.5   // Much more in front (positive Z)
        );
        
        // Make camera look at the center of the model
        this.camera.lookAt(center);
        
        // Update controls target
        if (this.controls) {
            this.controls.target.copy(center);
            this.controls.update();
        }
        
        // Force render to update the view
        this.render();
        
        console.log('Camera positioned to fit model at center:', center);
        console.log('Camera position:', this.camera.position);
        console.log('Distance from model:', distance);
    }
    
    showLoading() {
        this.container.innerHTML = `
            <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: #666;">
                <div style="width: 40px; height: 40px; border: 4px solid #f3f3f3; border-top: 4px solid #667eea; border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 16px;"></div>
                <p>Loading 3D model...</p>
            </div>
            <style>
                @keyframes spin {
                    0% { transform: rotate(0deg); }
                    100% { transform: rotate(360deg); }
                }
            </style>
        `;
    }
    
    hideLoading() {
        // Restore the renderer
        this.container.innerHTML = '';
        this.container.appendChild(this.renderer.domElement);
    }
    
    showError(message) {
        this.container.innerHTML = `
            <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: #dc3545; text-align: center; padding: 20px;">
                <div style="font-size: 48px; margin-bottom: 16px;">⚠️</div>
                <h4>Error Loading 3D Model</h4>
                <p>${message}</p>
                <button onclick="location.reload()" style="margin-top: 16px; padding: 8px 16px; background: #667eea; color: white; border: none; border-radius: 4px; cursor: pointer;">Retry</button>
            </div>
        `;
    }
    
    resetCamera() {
        if (this.parser && this.parser.boundingBox) {
            // If model is loaded, fit camera to model
            this.fitCameraToModel();
        } else {
            // Default position when no model is loaded
            this.camera.position.set(10, 10, 10);
            this.camera.lookAt(0, 0, 0);
            if (this.controls) {
                this.controls.target.set(0, 0, 0);
                this.controls.update();
            }
        }
    }
    
    toggleFullscreen() {
        if (!document.fullscreenElement) {
            this.container.requestFullscreen();
        } else {
            document.exitFullscreen();
        }
    }
    
    // Manual camera fit function that can be called externally
    fitCamera() {
        this.fitCameraToModel();
    }
    
    // Ensure camera properties display is available
    ensureCameraPropertiesDisplay() {
        // Check if camera properties display already exists
        const existingButton = this.container.querySelector('button[data-camera-info="true"]');
        const existingPanel = document.getElementById('camera-properties');
        
        if (!existingButton || !existingPanel) {
            this.addCameraPropertiesDisplay();
        }
    }
    
    // Ensure navigation controls are available
    ensureNavigationControls() {
        // Check if navigation controls already exist
        const existingNavPanel = document.getElementById('navigation-controls');
        
        if (!existingNavPanel) {
            this.addNavigationControls();
        }
    }
    
    // Add navigation controls
    addNavigationControls() {
        // Create navigation panel
        const navPanel = document.createElement('div');
        navPanel.id = 'navigation-controls';
        navPanel.style.cssText = `
            position: absolute;
            bottom: 20px;
            right: 20px;
            display: flex;
            flex-direction: column;
            gap: 10px;
            z-index: 1000;
        `;
        
        // Create navigation buttons
        const buttons = [
            { id: 'nav-up', text: '↑', title: 'Move Up' },
            { id: 'nav-down', text: '↓', title: 'Move Down' },
            { id: 'nav-left', text: '←', title: 'Move Left' },
            { id: 'nav-right', text: '→', title: 'Move Right' },
            { id: 'nav-forward', text: '↗', title: 'Move Forward' },
            { id: 'nav-backward', text: '↙', title: 'Move Backward' },
            { id: 'nav-zoom-in', text: '🔍+', title: 'Zoom In' },
            { id: 'nav-zoom-out', text: '🔍-', title: 'Zoom Out' },
            { id: 'nav-focus', text: '🏠', title: 'Focus on Model' }
        ];
        
        buttons.forEach(button => {
            const btn = document.createElement('button');
            btn.id = button.id;
            btn.textContent = button.text;
            btn.title = button.title;
            btn.style.cssText = `
                width: 40px;
                height: 40px;
                background: rgba(102, 126, 234, 0.9);
                color: white;
                border: none;
                border-radius: 50%;
                cursor: pointer;
                font-size: 16px;
                display: flex;
                align-items: center;
                justify-content: center;
                transition: all 0.2s;
                box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            `;
            
            btn.onmouseover = () => {
                btn.style.background = 'rgba(102, 126, 234, 1)';
                btn.style.transform = 'scale(1.1)';
            };
            
            btn.onmouseout = () => {
                btn.style.background = 'rgba(102, 126, 234, 0.9)';
                btn.style.transform = 'scale(1)';
            };
            
            navPanel.appendChild(btn);
        });
        
        this.container.appendChild(navPanel);
        
        // Add event listeners after buttons are created
        setTimeout(() => {
            this.setupNavigationEvents();
        }, 100);
    }
    
    // Setup navigation event listeners
    setupNavigationEvents() {
        const moveDistance = 50; // Distance to move per click
        
        console.log('Setting up navigation events...');
        
        // Up/Down movement
        const navUp = document.getElementById('nav-up');
        if (navUp) {
            navUp.addEventListener('click', () => {
                console.log('Moving up');
                this.camera.position.y += moveDistance;
                if (this.controls) this.controls.target.copy(this.controls.target);
                this.render();
            });
        }
        
        const navDown = document.getElementById('nav-down');
        if (navDown) {
            navDown.addEventListener('click', () => {
                console.log('Moving down');
                this.camera.position.y -= moveDistance;
                if (this.controls) this.controls.update();
                this.render();
            });
        }
        
        // Left/Right movement
        const navLeft = document.getElementById('nav-left');
        if (navLeft) {
            navLeft.addEventListener('click', () => {
                console.log('Moving left');
                this.camera.position.x -= moveDistance;
                if (this.controls) this.controls.update();
                this.render();
            });
        }
        
        const navRight = document.getElementById('nav-right');
        if (navRight) {
            navRight.addEventListener('click', () => {
                console.log('Moving right');
                this.camera.position.x += moveDistance;
                if (this.controls) this.controls.update();
                this.render();
            });
        }
        
        // Forward/Backward movement
        const navForward = document.getElementById('nav-forward');
        if (navForward) {
            navForward.addEventListener('click', () => {
                console.log('Moving forward');
                this.camera.position.z -= moveDistance;
                if (this.controls) this.controls.update();
                this.render();
            });
        }
        
        const navBackward = document.getElementById('nav-backward');
        if (navBackward) {
            navBackward.addEventListener('click', () => {
                console.log('Moving backward');
                this.camera.position.z += moveDistance;
                if (this.controls) this.controls.update();
                this.render();
            });
        }
        
        // Zoom controls
        const navZoomIn = document.getElementById('nav-zoom-in');
        if (navZoomIn) {
            navZoomIn.addEventListener('click', () => {
                console.log('Zooming in');
                const direction = new THREE.Vector3();
                this.camera.getWorldDirection(direction);
                this.camera.position.add(direction.multiplyScalar(moveDistance * 0.5));
                if (this.controls) this.controls.update();
                this.render();
            });
        }
        
        const navZoomOut = document.getElementById('nav-zoom-out');
        if (navZoomOut) {
            navZoomOut.addEventListener('click', () => {
                console.log('Zooming out');
                const direction = new THREE.Vector3();
                this.camera.getWorldDirection(direction);
                this.camera.position.add(direction.multiplyScalar(-moveDistance * 0.5));
                if (this.controls) this.controls.update();
                this.render();
            });
        }
        
        // Focus on model
        const navFocus = document.getElementById('nav-focus');
        if (navFocus) {
            navFocus.addEventListener('click', () => {
                console.log('Focusing on model');
                this.fitCameraToModel();
            });
        }
        
        // Reset view is handled by the main Reset button in the viewer controls
        
        console.log('Navigation events setup complete');
    }
    
    // Add camera properties display
    addCameraPropertiesDisplay() {
        // Create camera properties panel
        const propertiesPanel = document.createElement('div');
        propertiesPanel.id = 'camera-properties';
        propertiesPanel.style.cssText = `
            position: absolute;
            top: 10px;
            left: 10px;
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 10px;
            border-radius: 5px;
            font-family: monospace;
            font-size: 12px;
            z-index: 1000;
            max-width: 300px;
            display: none;
        `;
        
        // Add toggle button
        const toggleButton = document.createElement('button');
        toggleButton.textContent = '📷 Camera Info';
        toggleButton.setAttribute('data-camera-info', 'true');
        toggleButton.style.cssText = `
            position: absolute;
            top: 10px;
            left: 10px;
            background: #667eea;
            color: white;
            border: none;
            padding: 8px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            z-index: 1001;
        `;
        
        toggleButton.onclick = () => {
            if (propertiesPanel.style.display === 'none') {
                propertiesPanel.style.display = 'block';
                this.updateCameraProperties();
            } else {
                propertiesPanel.style.display = 'none';
            }
        };
        
        this.container.appendChild(toggleButton);
        this.container.appendChild(propertiesPanel);
        
        // Update properties when camera moves
        if (this.controls) {
            this.controls.addEventListener('change', () => {
                this.updateCameraProperties();
            });
        }
    }
    
    // Update camera properties display
    updateCameraProperties() {
        const propertiesPanel = document.getElementById('camera-properties');
        if (!propertiesPanel) return;
        
        const pos = this.camera.position;
        const target = this.controls ? this.controls.target : new THREE.Vector3(0, 0, 0);
        
        // Calculate relative position from model center
        const center = this.parser && this.parser.boundingBox ? this.parser.boundingBox.getCenter(new THREE.Vector3()) : new THREE.Vector3(0, 0, 0);
        const relativePos = {
            x: pos.x - center.x,
            y: pos.y - center.y,
            z: pos.z - center.z
        };
        
        propertiesPanel.innerHTML = `
            <div style="margin-bottom: 10px; font-weight: bold;">Camera Properties</div>
            <div><strong>Absolute Position:</strong></div>
            <div>X: ${pos.x.toFixed(2)}</div>
            <div>Y: ${pos.y.toFixed(2)}</div>
            <div>Z: ${pos.z.toFixed(2)}</div>
            <div style="margin-top: 10px;"><strong>Relative to Center:</strong></div>
            <div>X: ${relativePos.x.toFixed(2)}</div>
            <div>Y: ${relativePos.y.toFixed(2)}</div>
            <div>Z: ${relativePos.z.toFixed(2)}</div>
            <div style="margin-top: 10px;"><strong>Generic Code:</strong></div>
            <div style="background: #333; padding: 5px; border-radius: 3px; margin-top: 5px; font-size: 10px;">
                this.camera.position.set(<br>
                &nbsp;&nbsp;center.x + ${relativePos.x.toFixed(2)},<br>
                &nbsp;&nbsp;center.y + ${relativePos.y.toFixed(2)},<br>
                &nbsp;&nbsp;center.z + ${relativePos.z.toFixed(2)}<br>
                );
            </div>
            <button onclick="navigator.clipboard.writeText('this.camera.position.set(center.x + ${relativePos.x.toFixed(2)}, center.y + ${relativePos.y.toFixed(2)}, center.z + ${relativePos.z.toFixed(2)});')" 
                    style="margin-top: 10px; padding: 5px; background: #28a745; color: white; border: none; border-radius: 3px; cursor: pointer; font-size: 10px;">
                📋 Copy Generic Code
            </button>
        `;
    }
    
    // Aggressive camera fit for very close viewing
    zoomToModel() {
        if (!this.parser || !this.parser.boundingBox) {
            console.log('No parser or bounding box available for zoom');
            return;
        }
        
        const bbox = this.parser.boundingBox;
        const center = bbox.getCenter(new THREE.Vector3());
        const size = bbox.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);
        
        if (maxDim === 0) {
            console.log('Invalid bounding box for zoom');
            return;
        }
        
        // Very close distance
        const distance = maxDim * 0.15;
        
        // Position camera for top-down 2D map view (close)
        this.camera.position.set(
            center.x,                   // Centered horizontally
            center.y + distance * 1.5, // High above the model (top-down view)
            center.z                    // Centered horizontally
        );
        
        this.camera.lookAt(center);
        
        if (this.controls) {
            this.controls.target.copy(center);
            this.controls.update();
        }
        
        this.render();
        console.log('Zoomed to model - very close view');
    }
}

// Initialize viewer when page loads
document.addEventListener('DOMContentLoaded', function() {
    // Wait for Three.js to load
    const checkThree = setInterval(() => {
        if (typeof THREE !== 'undefined' && typeof THREE.OrbitControls !== 'undefined') {
            clearInterval(checkThree);
            window.viewer = new CityJSONViewer('viewer');
        }
    }, 100);
});
