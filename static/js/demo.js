// 3dSAGER Demo JavaScript
let currentSource = 'A';
let currentSessionId = null;
let locationMap = null;
let selectedFile = null; // Store selected file path
let selectedBuildingId = null; // Store selected building ID
let selectedBuildingData = null; // Store selected building data
let featuresLoaded = false; // Track if features have been calculated for current file
let bkafiLoaded = false; // Track if BKAFI results have been loaded
let buildingFeaturesCache = {}; // Cache features for all buildings
let buildingBkafiCache = {}; // Cache BKAFI pairs for buildings
let pipelineState = {
    step1Completed: false, // Geometric Featurization
    step2Completed: false, // BKAFI Blocking
    step3Completed: false  // Entity Resolution
};

// Initialize when page loads
document.addEventListener('DOMContentLoaded', function() {
    console.log('3dSAGER Demo initialized');
    loadDataFiles();
    initLocationMap();
    updatePipelineUI(); // Initialize pipeline UI
});

// Load data files from API
function loadDataFiles() {
    fetch('/api/data/files')
        .then(response => response.json())
        .then(data => {
            console.log('Files loaded:', data);
            renderFileList('A', data.source_a);
            renderFileList('B', data.source_b);
        })
        .catch(error => {
            console.error('Error loading files:', error);
        });
}

// Render file list
function renderFileList(source, files) {
    const container = document.getElementById(`files${source}`);
    container.innerHTML = '';
    
    files.forEach(file => {
        const fileItem = document.createElement('div');
        fileItem.className = 'file-item';
        fileItem.innerHTML = `
            <div class="file-name">${file.filename}</div>
            <div class="file-size">${(file.size / 1024 / 1024).toFixed(2)} MB</div>
        `;
        fileItem.onclick = () => selectFile(file.path, source);
        container.appendChild(fileItem);
    });
}

// Show source tab
function showSource(source) {
    // Update tabs
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelector(`[onclick="showSource('${source}')"]`).classList.add('active');
    
    // Update content
    document.querySelectorAll('.source-content').forEach(content => content.classList.remove('active'));
    document.getElementById(`source${source}`).classList.add('active');
    
    currentSource = source;
}

// Select a file
function selectFile(filePath, source) {
    console.log('Selecting file:', filePath, source);
    
    // Allow selecting from any source (Candidates or Index)
    // Users can view files from both sources in any order
    // Reset pipeline state and store file only if from Candidates (for pipeline steps)
    if (source === 'A') {
        resetPipelineState();
        selectedFile = filePath; // Store selected file for pipeline steps
        // Enable step 1 button when candidates file is selected
        document.getElementById('step-btn-1').disabled = false;
    }
    
    // Call API to select file
    fetch('/api/data/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: filePath, source: source })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            currentSessionId = data.session_id;
            loadFileInViewer(filePath);
        } else {
            console.error('Error selecting file:', data.error);
        }
    })
    .catch(error => {
        console.error('Error selecting file:', error);
    });
}

// Reset pipeline state
function resetPipelineState() {
    pipelineState = {
        step1Completed: false,
        step2Completed: false,
        step3Completed: false
    };
    selectedBuildingId = null;
    selectedBuildingData = null;
    featuresLoaded = false;
    bkafiLoaded = false;
    buildingFeaturesCache = {};
    buildingBkafiCache = {};
    updatePipelineUI();
}

// Initialize location map
let cityPolygon = null; // Store the city polygon layer

function initLocationMap() {
    // Wait for Leaflet to load
    if (typeof L === 'undefined') {
        setTimeout(initLocationMap, 100);
        return;
    }
    
    try {
        // Initialize Leaflet map (The Hague coordinates)
        locationMap = L.map('location-map').setView([52.0705, 4.3007], 13);
        
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors',
            maxZoom: 19
        }).addTo(locationMap);
        
        // Add marker for The Hague
        L.marker([52.0705, 4.3007])
            .addTo(locationMap)
            .bindPopup('The Hague, Netherlands<br>3dSAGER Demo Location')
            .openPopup();
        
        console.log('Location map initialized');
    } catch (error) {
        console.error('Error initializing location map:', error);
    }
}

// Update map with city bounds
function updateMapWithCityBounds(bounds) {
    if (!locationMap || !bounds) {
        console.warn('Cannot update map: locationMap or bounds missing', { locationMap: !!locationMap, bounds });
        return;
    }
    
    // Validate bounds
    if (!bounds.min || !bounds.max || 
        typeof bounds.min.lat !== 'number' || typeof bounds.min.lon !== 'number' ||
        typeof bounds.max.lat !== 'number' || typeof bounds.max.lon !== 'number') {
        console.error('Invalid bounds format:', bounds);
        return;
    }
    
    // Validate coordinate ranges (lat: -90 to 90, lon: -180 to 180)
    if (bounds.min.lat < -90 || bounds.max.lat > 90 || 
        bounds.min.lon < -180 || bounds.max.lon > 180) {
        console.error('Bounds out of valid range:', bounds);
        return;
    }
    
    // Check if bounds are reasonable (not all zeros or same point)
    if (Math.abs(bounds.max.lat - bounds.min.lat) < 0.0001 || 
        Math.abs(bounds.max.lon - bounds.min.lon) < 0.0001) {
        console.warn('Bounds too small (likely a point, not an area):', bounds);
    }
    
    try {
        // Remove existing polygon if any
        if (cityPolygon) {
            locationMap.removeLayer(cityPolygon);
            cityPolygon = null;
        }
        
        console.log('Creating polygon with bounds:', {
            min: { lat: bounds.min.lat, lon: bounds.min.lon },
            max: { lat: bounds.max.lat, lon: bounds.max.lon },
            center: bounds.center
        });
        
        // Create rectangle polygon from bounds
        const polygonBounds = [
            [bounds.min.lat, bounds.min.lon], // Southwest corner
            [bounds.min.lat, bounds.max.lon], // Southeast corner
            [bounds.max.lat, bounds.max.lon], // Northeast corner
            [bounds.max.lat, bounds.min.lon], // Northwest corner
            [bounds.min.lat, bounds.min.lon]  // Close the polygon
        ];
        
        // Create and add polygon
        cityPolygon = L.polygon(polygonBounds, {
            color: '#667eea',
            fillColor: '#667eea',
            fillOpacity: 0.3,
            weight: 2
        }).addTo(locationMap);
        
        // Fit map to show the polygon with some padding
        locationMap.fitBounds(cityPolygon.getBounds(), {
            padding: [20, 20], // Add padding around the bounds
            maxZoom: 15 // Don't zoom in too much
        });
        
        // Add popup to polygon with bounds info
        const boundsInfo = `City Model Bounds<br>
            Lat: ${bounds.min.lat.toFixed(6)} to ${bounds.max.lat.toFixed(6)}<br>
            Lon: ${bounds.min.lon.toFixed(6)} to ${bounds.max.lon.toFixed(6)}`;
        cityPolygon.bindPopup(boundsInfo).openPopup();
        
        console.log('Map updated successfully with city bounds');
    } catch (error) {
        console.error('Error updating map with city bounds:', error);
        console.error('Bounds that caused error:', bounds);
    }
}

// Map update callback disabled to improve performance
// window.onCityJSONLoaded = function(bounds) {
//     console.log('CityJSON loaded, updating map with bounds:', bounds);
//     updateMapWithCityBounds(bounds);
// };

// Load file in 3D viewer
function loadFileInViewer(filePath) {
    console.log('Loading file in viewer:', filePath);
    console.log('Viewer available:', !!window.viewer);
    console.log('Cesium available:', typeof Cesium !== 'undefined');
    
    // Wait for viewer to be ready (with retry)
    const tryLoad = (attempts = 0) => {
        if (window.viewer && window.viewer.loadCityJSON) {
            // Use the file path as-is (it should already be in the correct format from the API)
            // The path from the API is already relative to the data directory
            console.log('Using file path:', filePath);
            try {
                window.viewer.loadCityJSON(filePath);
            } catch (error) {
                console.error('Error calling loadCityJSON:', error);
                const viewer = document.getElementById('viewer');
                if (viewer) {
                    viewer.innerHTML = `
                        <div class="placeholder">
                            <div class="placeholder-icon">⚠️</div>
                            <p>Error loading file: ${error.message}</p>
                        </div>
                    `;
                }
            }
            
            // Also try to fit camera after a delay
            setTimeout(() => {
                if (window.viewer && window.viewer.zoomToModel) {
                    window.viewer.zoomToModel();
                }
            }, 1000);
        } else if (attempts < 20) {
            // Retry up to 20 times (2 seconds total)
            console.log(`Waiting for viewer to initialize... (attempt ${attempts + 1})`);
            setTimeout(() => tryLoad(attempts + 1), 100);
        } else {
            // Show error after retries exhausted
            console.error('Cesium viewer not available after waiting');
            const viewer = document.getElementById('viewer');
            if (viewer) {
                let errorMsg = '3D Viewer not ready. ';
                if (typeof Cesium === 'undefined') {
                    errorMsg += 'Cesium library failed to load. Check your internet connection and Cesium CDN.';
                } else {
                    errorMsg += 'Viewer initialization failed. Please refresh the page.';
                }
                viewer.innerHTML = `
                    <div class="placeholder">
                        <div class="placeholder-icon">⚠️</div>
                        <p>${errorMsg}</p>
                        <button onclick="location.reload()" style="margin-top: 10px; padding: 8px 16px; background: #667eea; color: white; border: none; border-radius: 4px; cursor: pointer;">Refresh Page</button>
                    </div>
                `;
            }
        }
    };
    
    tryLoad();
}

// Show building properties window
function showBuildingProperties(buildingId, cityObject) {
    selectedBuildingId = buildingId;
    selectedBuildingData = cityObject;
    
    const propsWindow = document.getElementById('building-properties-window');
    const propsNameEl = document.getElementById('building-props-name');
    const propsIdEl = document.getElementById('building-props-id');
    const propsListEl = document.getElementById('properties-list');
    const calcBtn = document.getElementById('calc-features-btn');
    const bkafiBtn = document.getElementById('run-bkafi-btn');
    
    if (!propsWindow || !propsNameEl || !propsIdEl || !propsListEl) {
        console.error('Building properties window elements not found');
        return;
    }
    
    // Show only the building ID (no name)
    propsNameEl.textContent = '';
    propsIdEl.textContent = `ID: ${buildingId}`;
    
    // Clear properties list
    propsListEl.innerHTML = '';
    
    // Hide BKAFI button initially
    if (bkafiBtn) {
        bkafiBtn.style.display = 'none';
    }
    
    // Enable calculate features button (only if a candidates file is selected)
    // Pipeline steps only work with candidates files
    // Check if we have a selected candidates file (source A)
    const isCandidatesFile = selectedFile && currentSource === 'A';
    
    if (isCandidatesFile) {
        if (featuresLoaded) {
            // Features already calculated, load and show them
            calcBtn.disabled = true;
            calcBtn.textContent = 'Features Calculated';
            calcBtn.style.background = '#28a745';
            loadBuildingFeatures(buildingId);
            
            // Also load BKAFI pairs if BKAFI has been run
            if (bkafiLoaded) {
                loadBuildingBkafiPairs(buildingId);
            }
        } else {
            // Features not calculated yet
            calcBtn.disabled = false;
            calcBtn.textContent = 'Calculate Geometric Features';
            calcBtn.style.background = '#667eea'; // Reset button color
        }
    } else {
        calcBtn.disabled = true;
        if (currentSource === 'B') {
            calcBtn.textContent = 'Select Candidates File for Pipeline';
        } else {
            calcBtn.textContent = 'Select Candidates File First';
        }
    }
    
    // Show the window
    propsWindow.style.display = 'block';
    
    // Add overlay
    let overlay = document.getElementById('properties-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'properties-overlay';
        overlay.className = 'properties-overlay';
        overlay.onclick = closeBuildingProperties;
        document.body.appendChild(overlay);
    }
    overlay.classList.add('active');
}

// Close building properties window
function closeBuildingProperties() {
    const propsWindow = document.getElementById('building-properties-window');
    const overlay = document.getElementById('properties-overlay');
    
    if (propsWindow) {
        propsWindow.style.display = 'none';
    }
    
    if (overlay) {
        overlay.classList.remove('active');
    }
}

// Calculate geometric features (Step 1) - for all buildings
function calculateGeometricFeatures() {
    if (!selectedFile) {
        alert('Please select a candidates file first.');
        return;
    }
    
    // Can be called from sidebar button or building properties window
    const stepBtn = document.getElementById('step-btn-1');
    const calcBtn = document.getElementById('calc-features-btn');
    
    // Update button states
    stepBtn.textContent = 'Loading...';
    stepBtn.disabled = true;
    if (calcBtn) {
        calcBtn.textContent = 'Calculating...';
        calcBtn.disabled = true;
    }
    
    // Call API to calculate features for all buildings
    fetch('/api/features/calculate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: selectedFile })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error calculating features:', data.error);
                alert('Error calculating geometric features: ' + data.error);
                stepBtn.textContent = 'Calculate Features';
                stepBtn.disabled = false;
                if (calcBtn) {
                    calcBtn.textContent = 'Calculate Geometric Features';
                    calcBtn.disabled = false;
                }
                return;
            }
            
            console.log('Features calculated:', data.message);
            
            // Mark step 1 as completed
            pipelineState.step1Completed = true;
            featuresLoaded = true;
            updatePipelineUI();
            
            // Enable step 2
            document.getElementById('step-btn-2').disabled = false;
            
            stepBtn.textContent = 'Completed';
            stepBtn.style.background = '#28a745';
            
            // If building properties window is open, show features
            if (selectedBuildingId) {
                loadBuildingFeatures(selectedBuildingId);
            }
            
            if (calcBtn) {
                calcBtn.textContent = 'Features Calculated';
                calcBtn.style.background = '#28a745';
            }
        })
        .catch(error => {
            console.error('Error calculating features:', error);
            alert('Error calculating geometric features: ' + error.message);
            stepBtn.textContent = 'Calculate Features';
            stepBtn.disabled = false;
            if (calcBtn) {
                calcBtn.textContent = 'Calculate Geometric Features';
                calcBtn.disabled = false;
            }
        });
}

// Load features for a specific building
function loadBuildingFeatures(buildingId) {
    if (!selectedFile) {
        console.warn('Cannot load features: no file selected');
        return;
    }
    
    if (!featuresLoaded) {
        console.warn('Features not yet calculated. Please run Step 1 first.');
        return;
    }
    
    console.log('Loading features for building:', buildingId);
    
    // Check cache first
    if (buildingFeaturesCache[buildingId]) {
        console.log('Using cached features for building:', buildingId);
        showGeometricFeatures(buildingFeaturesCache[buildingId]);
        return;
    }
    
    // Load from API
    console.log('Fetching features from API for building:', buildingId);
    fetch(`/api/building/features/${buildingId}?file=${encodeURIComponent(selectedFile)}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error loading features:', data.error);
                const propsListEl = document.getElementById('properties-list');
                if (propsListEl) {
                    propsListEl.innerHTML = `<p style="color: red; padding: 20px;">Error loading features: ${data.error}</p>`;
                }
                return;
            }
            
            // Check if building was found
            if (data.found === false || !data.features || Object.keys(data.features).length === 0) {
                console.warn('No features returned for building:', buildingId);
                const propsListEl = document.getElementById('properties-list');
                if (propsListEl) {
                    const message = data.message || 'No features found for this building. This building may not be in the feature calculation dataset.';
                    propsListEl.innerHTML = `<div style="padding: 20px; color: #666;">
                        <p style="margin: 0 0 10px 0;">${message}</p>
                        <p style="margin: 0; font-size: 12px; color: #999;">Building ID: ${buildingId}</p>
                    </div>`;
                }
                return;
            }
            
            console.log('Features loaded successfully:', Object.keys(data.features).length, 'features');
            
            // Cache the features
            buildingFeaturesCache[buildingId] = data.features;
            
            // Show all features in properties window
            showGeometricFeatures(data.features);
        })
        .catch(error => {
            console.error('Error loading building features:', error);
            const propsListEl = document.getElementById('properties-list');
            if (propsListEl) {
                propsListEl.innerHTML = `<p style="color: red; padding: 20px;">Error: ${error.message}</p>`;
            }
        });
}

// Show geometric features in properties window
function showGeometricFeatures(features) {
    const propsListEl = document.getElementById('properties-list');
    const calcBtn = document.getElementById('calc-features-btn');
    const bkafiBtn = document.getElementById('run-bkafi-btn');
    if (!propsListEl || !features) {
        console.warn('Cannot show features: propsListEl or features missing', { propsListEl: !!propsListEl, features: !!features });
        return;
    }
    
    console.log('Displaying features for building:', selectedBuildingId);
    console.log('Number of features:', Object.keys(features).length);
    console.log('Feature keys:', Object.keys(features));
    
    // Remove existing geometric features section if it exists
    const existingFeaturesSection = propsListEl.querySelector('.geometric-features-section');
    if (existingFeaturesSection) {
        existingFeaturesSection.remove();
    }
    
    // Create container for geometric features
    const featuresContainer = document.createElement('div');
    featuresContainer.className = 'geometric-features-section';
    
    // Add heading
    const heading = document.createElement('div');
    heading.className = 'property-separator';
    const featureCount = Object.keys(features).length;
    heading.innerHTML = `<h4 style="margin: 0 0 15px 0; color: #667eea; font-size: 16px;">Geometric Features (${featureCount})</h4>`;
    featuresContainer.appendChild(heading);
    
    // Sort features alphabetically for better readability
    const sortedKeys = Object.keys(features).sort();
    
    // Add all features from the joblib file
    sortedKeys.forEach(key => {
        const value = features[key];
        const propItem = document.createElement('div');
        propItem.className = 'property-item feature-item';
        
        // Format the value appropriately
        let displayValue = value;
        if (typeof value === 'number') {
            displayValue = value.toFixed(4);
        } else if (Array.isArray(value)) {
            displayValue = `[${value.length} items]`;
        } else if (typeof value === 'object' && value !== null) {
            displayValue = JSON.stringify(value);
        }
        
        propItem.innerHTML = `
            <div class="property-key">${key}:</div>
            <div class="property-value">${displayValue}</div>
        `;
        featuresContainer.appendChild(propItem);
    });
    
    // Insert geometric features AFTER BKAFI section if it exists, otherwise at the beginning
    // This ensures BKAFI pairs always appear above geometric features
    const existingBkafiSection = propsListEl.querySelector('.bkafi-pairs-section');
    if (existingBkafiSection) {
        // Insert after BKAFI section
        existingBkafiSection.parentNode.insertBefore(featuresContainer, existingBkafiSection.nextSibling);
    } else {
        // Insert at the beginning if no BKAFI section
        propsListEl.insertBefore(featuresContainer, propsListEl.firstChild);
    }
    
    // Disable and update button text
    if (calcBtn) {
        calcBtn.disabled = true;
        calcBtn.textContent = 'Features Calculated';
        calcBtn.style.background = '#28a745';
    }
    
    // Show and enable BKAFI button if features exist and Step 1 is completed
    if (bkafiBtn && featureCount > 0 && pipelineState.step1Completed) {
        bkafiBtn.style.display = 'block';
        if (!pipelineState.step2Completed) {
            bkafiBtn.disabled = false;
            bkafiBtn.textContent = 'Run BKAFI';
            bkafiBtn.style.background = '#667eea';
        } else {
            bkafiBtn.disabled = true;
            bkafiBtn.textContent = 'BKAFI Completed';
            bkafiBtn.style.background = '#28a745';
        }
    }
}

// Run BKAFI (Step 2)
function runBKAFI() {
    if (!pipelineState.step1Completed) {
        alert('Please complete Geometric Featurization first.');
        return;
    }
    
    console.log('Loading BKAFI results');
    
    const stepBtn = document.getElementById('step-btn-2');
    stepBtn.textContent = 'Loading...';
    stepBtn.disabled = true;
    
    // Call API to load BKAFI results from pkl file
    fetch('/api/bkafi/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error loading BKAFI results:', data.error);
                alert('Error loading BKAFI results: ' + data.error);
                stepBtn.textContent = 'Run BKAFI';
                stepBtn.disabled = false;
                return;
            }
            
            console.log('BKAFI results loaded:', data.message);
            
            // Mark step 2 as completed
            pipelineState.step2Completed = true;
            bkafiLoaded = true;
            updatePipelineUI();
            
            // Enable step 3
            document.getElementById('step-btn-3').disabled = false;
            
            stepBtn.textContent = 'Completed';
            stepBtn.style.background = '#28a745';
            
            // Update BKAFI button in properties window if open
            const bkafiBtn = document.getElementById('run-bkafi-btn');
            if (bkafiBtn) {
                bkafiBtn.disabled = true;
                bkafiBtn.textContent = 'BKAFI Completed';
                bkafiBtn.style.background = '#28a745';
            }
            
            // If building properties window is open, load BKAFI pairs
            if (selectedBuildingId) {
                loadBuildingBkafiPairs(selectedBuildingId);
            }
        })
        .catch(error => {
            console.error('Error loading BKAFI results:', error);
            alert('Error loading BKAFI results: ' + error.message);
            stepBtn.textContent = 'Run BKAFI';
            stepBtn.disabled = false;
        });
}

// Load BKAFI pairs for a specific building
function loadBuildingBkafiPairs(buildingId) {
    if (!selectedFile) return;
    
    if (!bkafiLoaded) {
        console.warn('BKAFI results not loaded yet');
        return;
    }
    
    // Check cache first
    if (buildingBkafiCache[buildingId]) {
        showBkafiPairs(buildingBkafiCache[buildingId]);
        return;
    }
    
    // Load from API
    console.log('Fetching BKAFI pairs from API for building:', buildingId);
    fetch(`/api/building/bkafi/${buildingId}?file=${encodeURIComponent(selectedFile)}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error loading BKAFI pairs:', data.error);
                return;
            }
            
            if (!data.pairs || data.pairs.length === 0) {
                console.warn('No BKAFI pairs returned for building:', buildingId);
                return;
            }
            
            console.log('BKAFI pairs loaded successfully:', data.pairs.length, 'pairs');
            
            // Cache the pairs
            buildingBkafiCache[buildingId] = data.pairs;
            
            // Show pairs in properties window - pass the buildingId explicitly
            showBkafiPairs(data.pairs, buildingId);
        })
        .catch(error => {
            console.error('Error loading BKAFI pairs:', error);
        });
}

// Show BKAFI pairs in properties window
function showBkafiPairs(pairs, buildingId = null) {
    // Use provided buildingId or fall back to selectedBuildingId
    const currentBuildingId = buildingId || selectedBuildingId;
    
    const propsListEl = document.getElementById('properties-list');
    if (!propsListEl || !pairs || pairs.length === 0) return;
    
    if (!currentBuildingId) {
        console.error('Cannot show BKAFI pairs: no building ID available');
        return;
    }
    
    // Remove existing BKAFI section if it exists
    const existingBkafiSection = propsListEl.querySelector('.bkafi-pairs-section');
    if (existingBkafiSection) {
        existingBkafiSection.remove();
    }
    
    // Create container for BKAFI pairs
    const bkafiContainer = document.createElement('div');
    bkafiContainer.className = 'bkafi-pairs-section';
    
    // Store the building ID and pairs in data attributes for the button
    bkafiContainer.setAttribute('data-building-id', currentBuildingId);
    bkafiContainer.setAttribute('data-pairs', JSON.stringify(pairs));
    
    // Add separator and heading
    const separator = document.createElement('div');
    separator.className = 'property-separator';
    separator.innerHTML = `<h4 style="margin: 0 0 15px 0; color: #667eea; font-size: 16px;">BKAFI Pairs (${pairs.length})</h4>`;
    bkafiContainer.appendChild(separator);
    
    // Add pairs (without prediction/true label - those will be shown after entity resolution)
    pairs.forEach((pair, index) => {
        const pairItem = document.createElement('div');
        pairItem.className = 'property-item feature-item';
        pairItem.style.background = '#f8f9fa';
        pairItem.style.borderLeft = '4px solid #667eea';
        pairItem.style.padding = '12px';
        pairItem.style.marginBottom = '8px';
        pairItem.style.borderRadius = '4px';
        
        pairItem.innerHTML = `
            <div style="margin-bottom: 6px;">
                <strong style="color: #333;">Pair ${index + 1}</strong>
            </div>
            <div style="font-size: 12px; color: #666;">
                <div><strong>Index Building ID:</strong> ${pair.index_id}</div>
            </div>
        `;
        bkafiContainer.appendChild(pairItem);
    });
    
    // Add button to view pairs visually - use the building ID and pairs from this specific section
    const viewButton = document.createElement('button');
    viewButton.className = 'action-btn';
    viewButton.style.marginTop = '15px';
    viewButton.style.width = '100%';
    viewButton.textContent = 'View Pairs Visually';
    viewButton.onclick = () => {
        // Get the building ID and pairs from the container's data attributes
        const containerBuildingId = bkafiContainer.getAttribute('data-building-id');
        const containerPairs = JSON.parse(bkafiContainer.getAttribute('data-pairs'));
        console.log('View button clicked for building:', containerBuildingId, 'with', containerPairs.length, 'pairs');
        openBkafiComparisonWindow(containerBuildingId, containerPairs);
    };
    bkafiContainer.appendChild(viewButton);
    
    // Always insert BKAFI pairs at the very beginning (top of properties window)
    // This ensures BKAFI pairs always appear above geometric features
    propsListEl.insertBefore(bkafiContainer, propsListEl.firstChild);
    
    // If geometric features section exists, move it after BKAFI section
    const existingFeaturesSection = propsListEl.querySelector('.geometric-features-section');
    if (existingFeaturesSection && existingFeaturesSection !== bkafiContainer.nextSibling) {
        // Remove and re-insert after BKAFI section
        existingFeaturesSection.parentNode.removeChild(existingFeaturesSection);
        bkafiContainer.parentNode.insertBefore(existingFeaturesSection, bkafiContainer.nextSibling);
    }
}

// Open BKAFI comparison window
function openBkafiComparisonWindow(candidateBuildingId, pairs) {
    console.log('Opening BKAFI comparison window for building:', candidateBuildingId, 'with', pairs.length, 'pairs');
    
    const comparisonWindow = document.getElementById('bkafi-comparison-window');
    if (!comparisonWindow) {
        console.error('Comparison window not found');
        return;
    }
    
    // Store pairs data in the window for later use (for classifier results)
    comparisonWindow.setAttribute('data-candidate-id', candidateBuildingId);
    comparisonWindow.setAttribute('data-pairs', JSON.stringify(pairs));
    
    // Show window first
    comparisonWindow.style.display = 'flex';
    
    // Add overlay
    let overlay = document.getElementById('comparison-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'comparison-overlay';
        overlay.className = 'comparison-overlay';
        overlay.onclick = closeBkafiComparisonWindow;
        document.body.appendChild(overlay);
    }
    overlay.classList.add('active');
    
    // Get viewer elements BEFORE cleanup (so we have references)
    const candidateViewerEl = document.getElementById('comparison-viewer-candidate');
    const pairsViewersEl = document.getElementById('comparison-pairs-viewers');
    const candidateIdEl = document.getElementById('comparison-candidate-id');
    
    console.log(`Viewer elements found:`, {
        candidateViewerEl: !!candidateViewerEl,
        pairsViewersEl: !!pairsViewersEl,
        candidateIdEl: !!candidateIdEl
    });
    
    // Clean up old viewer instances (dispose Three.js viewers)
    cleanupComparisonViewers();
    
    // Completely clear and reset viewer elements AFTER cleanup
    if (candidateViewerEl) {
        // Clear all children and reset
        candidateViewerEl.innerHTML = '';
        candidateViewerEl.id = 'comparison-viewer-candidate'; // Reset ID
        candidateViewerEl.style.position = 'relative'; // Reset positioning
        candidateViewerEl.style.width = '100%'; // Ensure width
        candidateViewerEl.style.height = '180px'; // Same size as pair viewers
        
        // Add loading message
        const loadingDiv = document.createElement('div');
        loadingDiv.style.cssText = 'padding: 20px; text-align: center; color: #666;';
        loadingDiv.textContent = 'Loading candidate building...';
        candidateViewerEl.appendChild(loadingDiv);
        
        console.log('Candidate viewer element reset and ready');
    } else {
        console.error('Candidate viewer element not found!');
    }
    
    if (pairsViewersEl) {
        pairsViewersEl.innerHTML = '';
        console.log('Pairs viewers element cleared');
    } else {
        console.error('Pairs viewers element not found!');
    }
    
    if (candidateIdEl) {
        candidateIdEl.textContent = `Candidate: ${candidateBuildingId}`;
    }
    
    // Setup classifier results section - show button immediately (can be clicked before buildings finish loading)
    const classifierSection = document.getElementById('comparison-classifier-section');
    const showClassifierBtn = document.getElementById('show-classifier-results-btn');
    const classifierResults = document.getElementById('classifier-results');
    
    if (classifierSection && showClassifierBtn && classifierResults) {
        // Show classifier section immediately (button is available right away)
        classifierSection.style.display = 'block';
        classifierResults.innerHTML = '';
        showClassifierBtn.textContent = 'Show Classifier Results';
        showClassifierBtn.disabled = false;
        
        // Set up button click handler - use stored pairs from window
        showClassifierBtn.onclick = () => {
            // Get pairs from stored data
            const storedPairs = JSON.parse(comparisonWindow.getAttribute('data-pairs') || '[]');
            const storedCandidateId = comparisonWindow.getAttribute('data-candidate-id') || candidateBuildingId;
            showClassifierResultsInComparisonWindow(storedCandidateId, storedPairs);
        };
    }
    
    // Load candidate building first, then pairs sequentially
    // Always find the file for the candidate building to ensure we load the correct one
    const loadCandidate = () => {
        console.log(`=== LOADING CANDIDATE BUILDING ===`);
        console.log(`Candidate building ID: ${candidateBuildingId}`);
        
        // Get fresh reference to elements - try multiple ways to find it
        let candidateEl = document.getElementById('comparison-viewer-candidate');
        
        // If not found by ID, try to find by data attribute or class
        if (!candidateEl) {
            candidateEl = document.querySelector('[data-original-id="comparison-viewer-candidate"]');
        }
        if (!candidateEl) {
            candidateEl = document.querySelector('.comparison-viewer-container .comparison-viewer');
        }
        
        const candidateIdElRef = document.getElementById('comparison-candidate-id');
        
        console.log(`Candidate viewer element (fresh):`, candidateEl);
        console.log(`Candidate ID element (fresh):`, candidateIdElRef);
        
        if (!candidateEl) {
            console.error('Candidate viewer element is null! Trying to find it again...');
            // Try one more time after a short delay - maybe DOM isn't ready
            setTimeout(() => {
                let retryEl = document.getElementById('comparison-viewer-candidate');
                if (!retryEl) {
                    retryEl = document.querySelector('[data-original-id="comparison-viewer-candidate"]');
                }
                if (!retryEl) {
                    retryEl = document.querySelector('.comparison-viewer-container .comparison-viewer');
                }
                
                if (retryEl) {
                    console.log('Found candidate element on retry');
                    findAndLoadBuilding(candidateBuildingId, retryEl, candidateIdElRef, 'candidate', () => {
                        console.log(`=== CANDIDATE BUILDING LOADED ===`);
                        console.log(`Starting to load ${pairs.length} pairs`);
                        // Load pairs sequentially (button is already visible and can be clicked)
                        loadPairsSequentially(pairs.slice(0, 3), 0);
                    });
                } else {
                    console.error('Candidate element still not found after retry');
                    console.error('Available elements:', document.querySelectorAll('.comparison-viewer'));
                }
            }, 200);
            return;
        }
        
        findAndLoadBuilding(candidateBuildingId, candidateEl, candidateIdElRef, 'candidate', () => {
            // After candidate loads, start loading pairs
            console.log(`=== CANDIDATE BUILDING LOADED ===`);
            console.log(`Starting to load ${pairs.length} pairs`);
            // Load pairs sequentially (button is already visible and can be clicked)
            loadPairsSequentially(pairs.slice(0, 3), 0);
        });
    };
    
    // Create pair viewer containers first (so they're visible)
    const pairsToShow = pairs.slice(0, 3);
    console.log(`Creating ${pairsToShow.length} pair viewer containers`);
    
    pairsToShow.forEach((pair, index) => {
        const pairViewerEl = document.createElement('div');
        pairViewerEl.className = 'comparison-pair-item';
        pairViewerEl.id = `comparison-pair-${index}`;
        
        const pairLabel = document.createElement('div');
        pairLabel.className = 'comparison-pair-label';
        pairLabel.textContent = `Pair ${index + 1}`;
        pairViewerEl.appendChild(pairLabel);
        
        const pairViewer = document.createElement('div');
        pairViewer.className = 'comparison-pair-viewer';
        pairViewer.id = `comparison-viewer-pair-${index}`;
        pairViewer.style.position = 'relative';
        pairViewer.style.width = '100%';
        pairViewer.style.height = '180px'; // Same size as candidate viewer
        pairViewer.innerHTML = '<div style="padding: 20px; text-align: center; color: #999; font-size: 12px;">Waiting to load...</div>';
        pairViewerEl.appendChild(pairViewer);
        
        const pairIdEl = document.createElement('div');
        pairIdEl.className = 'viewer-building-id';
        pairIdEl.id = `comparison-pair-id-${index}`;
        pairIdEl.textContent = `Index: ${pair.index_id}`;
        pairViewerEl.appendChild(pairIdEl);
        
        if (pairsViewersEl) {
            pairsViewersEl.appendChild(pairViewerEl);
            console.log(`Added pair viewer container ${index} for building ${pair.index_id}`);
        } else {
            console.error(`Pairs viewers element is null, cannot add pair ${index}`);
        }
    });
    
    // Small delay to ensure DOM is ready, then start loading candidate
    setTimeout(() => {
        console.log(`Starting to load candidate building after DOM setup`);
        loadCandidate();
    }, 100);
}

// Load pairs sequentially (one at a time) to avoid overwhelming the browser
function loadPairsSequentially(pairs, currentIndex, onAllComplete = null) {
    if (currentIndex >= pairs.length) {
        console.log('All pairs loaded');
        if (onAllComplete) {
            onAllComplete();
        }
        return;
    }
    
    const pair = pairs[currentIndex];
    const pairViewer = document.getElementById(`comparison-viewer-pair-${currentIndex}`);
    const pairIdEl = document.getElementById(`comparison-pair-id-${currentIndex}`);
    
    if (!pairViewer || !pairIdEl) {
        console.error(`Pair viewer elements not found for index ${currentIndex}`);
        // Continue with next pair
        setTimeout(() => loadPairsSequentially(pairs, currentIndex + 1, onAllComplete), 100);
        return;
    }
    
    console.log(`Loading pair ${currentIndex + 1}/${pairs.length}: ${pair.index_id}`);
    
    // Use unique viewerType for each pair to avoid conflicts and ensure proper cleanup
    const viewerType = `pair-${currentIndex}`;
    
    // Find and load the building, then load next pair
    // Each pair gets its own viewer instance with dark red color
    findAndLoadBuilding(pair.index_id, pairViewer, pairIdEl, viewerType, () => {
        // After this pair loads, load the next one
        setTimeout(() => {
            loadPairsSequentially(pairs, currentIndex + 1, onAllComplete);
        }, 500); // Small delay between loads
    });
}

// Find which file contains a building and load it
function findAndLoadBuilding(buildingId, viewerEl, idEl, viewerType, onComplete) {
    console.log(`Finding file for building ${buildingId} (${viewerType})`);
    
    if (!viewerEl) {
        console.error(`Viewer element not found for ${viewerType}`);
        if (onComplete) onComplete();
        return;
    }
    
    // Update loading message without clearing the entire element (to preserve any existing structure)
    let existingLoading = viewerEl.querySelector('div[style*="padding: 20px"]');
    if (existingLoading) {
        existingLoading.textContent = 'Finding building file...';
        existingLoading.style.fontSize = '12px';
    } else {
        // Only add loading message if one doesn't exist
        existingLoading = document.createElement('div');
        existingLoading.style.cssText = 'padding: 20px; text-align: center; color: #666; font-size: 12px;';
        existingLoading.textContent = 'Finding building file...';
        viewerEl.appendChild(existingLoading);
    }
    
    // Store reference to loading element for cleanup
    const loadingElement = existingLoading;
    
    // Extract numeric ID if building ID has prefix (e.g., "bag_0518100000239978" -> "0518100000239978")
    const numericId = buildingId.replace(/^[^_]*_/, '');
    console.log(`Searching for building with ID: ${buildingId} (numeric: ${numericId})`);
    
    fetch(`/api/building/find-file/${encodeURIComponent(numericId)}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.error || !data.file_path) {
                console.error(`Error finding file for building ${buildingId}:`, data.error || data.message);
                const errorDiv = document.createElement('div');
                errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545;';
                errorDiv.textContent = `Building not found: ${data.error || data.message || 'Unknown error'}`;
                // Remove loading message and add error
                if (loadingElement && loadingElement.parentNode) {
                    loadingElement.parentNode.removeChild(loadingElement);
                }
                viewerEl.appendChild(errorDiv);
                if (onComplete) onComplete();
                return;
            }
            
            console.log(`Found building ${buildingId} in file ${data.file_path} (source: ${data.source})`);
            // Remove loading message before loading building
            if (loadingElement && loadingElement.parentNode) {
                loadingElement.parentNode.removeChild(loadingElement);
            }
            loadBuildingInComparisonViewer(buildingId, data.file_path, viewerEl, idEl, viewerType, onComplete);
        })
        .catch(error => {
            console.error(`Error finding file for building ${buildingId}:`, error);
            const errorDiv = document.createElement('div');
            errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545;';
            errorDiv.textContent = `Error: ${error.message}`;
            // Remove loading message and add error
            if (loadingElement && loadingElement.parentNode) {
                loadingElement.parentNode.removeChild(loadingElement);
            }
            viewerEl.appendChild(errorDiv);
            if (onComplete) onComplete();
        });
}

// Load a single building in a comparison viewer (shows ONLY that building)
function loadBuildingInComparisonViewer(buildingId, filePath, viewerEl, idEl, viewerType, onComplete) {
    console.log(`=== loadBuildingInComparisonViewer called ===`);
    console.log(`Building ID: ${buildingId}`);
    console.log(`File path: ${filePath}`);
    console.log(`Viewer type: ${viewerType}`);
    console.log(`Viewer element:`, viewerEl);
    console.log(`ID element:`, idEl);
    
    if (!viewerEl) {
        console.error(`Cannot load building ${buildingId}: viewer element is null`);
        if (onComplete) onComplete();
        return;
    }
    
    console.log(`Loading ONLY building ${buildingId} from ${filePath} in ${viewerType} viewer`);
    
    // Store original ID if it exists (so we can find the element later)
    const originalId = viewerEl.id || `comparison-viewer-${viewerType}`;
    
    // Create a unique container ID for this viewer (but keep original ID as data attribute)
    const containerId = `comparison-viewer-${viewerType}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    console.log(`Container ID: ${containerId}, Original ID: ${originalId}`);
    viewerEl.id = containerId;
    viewerEl.setAttribute('data-original-id', originalId); // Store original ID
    // Don't clear innerHTML - we'll add loading message as a child element instead
    // This prevents removing the Three.js canvas later
    viewerEl.style.position = 'relative'; // Ensure positioning context for loading message
    
    // Store viewer reference - use unique key per viewer type
    const viewerKey = `comparison-viewer-${viewerType}`;
    console.log(`Viewer key: ${viewerKey}`);
    
    // Extract numeric ID if building ID has prefix (e.g., "bag_0518100000239978" -> "0518100000239978")
    const numericId = buildingId.replace(/^[^_]*_/, '');
    console.log(`Loading building with ID: ${buildingId} (using numeric: ${numericId}) from file: ${filePath}`);
    
    // Load ONLY the single building (minimal CityJSON with just this building)
    fetch(`/api/building/single/${encodeURIComponent(numericId)}?file=${encodeURIComponent(filePath)}`)
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => Promise.reject(new Error(err.error || `HTTP ${response.status}`)));
            }
            return response.json();
        })
        .then(minimalCityJSON => {
            console.log(`=== Minimal CityJSON loaded ===`);
            console.log(`Building ID: ${buildingId}`);
            console.log(`CityJSON keys:`, Object.keys(minimalCityJSON));
            console.log(`CityJSON has ${Object.keys(minimalCityJSON.CityObjects || {}).length} city objects`);
            if (minimalCityJSON.CityObjects) {
                console.log(`City object IDs:`, Object.keys(minimalCityJSON.CityObjects));
            }
            
            // Clear any existing content but keep the container structure
            // Remove loading messages but keep the element itself
            const existingLoading = viewerEl.querySelector('div[style*="padding: 20px"]');
            if (existingLoading) {
                existingLoading.remove();
            }
            
            // Add new loading message
            const loadingDiv = document.createElement('div');
            loadingDiv.id = `loading-msg-${containerId}`;
            loadingDiv.style.cssText = 'padding: 20px; text-align: center; color: #666; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10; background: rgba(255,255,255,0.9); border-radius: 4px;';
            loadingDiv.textContent = 'Initializing viewer...';
            viewerEl.appendChild(loadingDiv);
            
            // Check if Three.js is loaded - wait for it if needed
            const checkThreeJS = (attempts = 0) => {
                if (typeof THREE !== 'undefined') {
                    initializeThreeViewer();
                } else if (attempts < 50) {
                    // Wait up to 5 seconds for Three.js to load
                    setTimeout(() => checkThreeJS(attempts + 1), 100);
                } else {
                    console.error('Three.js library failed to load after 5 seconds!');
                    const errorDiv = document.createElement('div');
                    errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10;';
                    errorDiv.textContent = 'Three.js library not loaded. Please refresh the page.';
                    viewerEl.appendChild(errorDiv);
                    if (onComplete) onComplete();
                }
            };
            
            const initializeThreeViewer = () => {
                setTimeout(() => {
                try {
                    console.log(`=== USING THREE.JS VIEWER (NOT CESIUM) ===`);
                    console.log(`Creating Three.js viewer for ${buildingId} in container ${containerId}`);
                    console.log(`Three.js available:`, typeof THREE !== 'undefined');
                    console.log(`ThreeBuildingViewer available:`, typeof ThreeBuildingViewer !== 'undefined');
                    
                    // Get or create loading message
                    let loadingMsg = viewerEl.querySelector(`#loading-msg-${containerId}`);
                    if (!loadingMsg) {
                        loadingMsg = document.createElement('div');
                        loadingMsg.id = `loading-msg-${containerId}`;
                        loadingMsg.style.cssText = 'padding: 20px; text-align: center; color: #666; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10; background: rgba(255,255,255,0.9); border-radius: 4px;';
                        loadingMsg.textContent = 'Initializing viewer...';
                        viewerEl.appendChild(loadingMsg);
                    }
                    
                    // Dispose old viewer if it exists
                    if (window[viewerKey]) {
                        try {
                            const oldViewer = window[viewerKey];
                            if (oldViewer.dispose) {
                                oldViewer.dispose();
                            }
                        } catch (e) {
                            console.warn('Error disposing old viewer:', e);
                        }
                        delete window[viewerKey];
                    }
                    
                    // Dispose old viewer if it exists
                    if (window[viewerKey]) {
                        try {
                            const oldViewer = window[viewerKey];
                            if (oldViewer.dispose) {
                                oldViewer.dispose();
                            }
                        } catch (e) {
                            console.warn('Error disposing old viewer:', e);
                        }
                        delete window[viewerKey];
                    }
                    
                    // Create Three.js viewer (lightweight, fast) - NOT Cesium!
                    // Set color based on viewer type: blue for candidate, dark red for pairs
                    const buildingColor = viewerType === 'candidate' ? 0x2196F3 : 0x8B0000; // Blue for candidate, dark red for pairs
                    const viewer = new ThreeBuildingViewer(containerId, buildingColor);
                    window[viewerKey] = viewer; // Store reference
                    console.log(`Stored viewer with key: ${viewerKey} for building: ${buildingId}`);
                    
                    // Wait for viewer to initialize
                    let attempts = 0;
                    const maxAttempts = 30; // 3 seconds max
                    const checkInitialized = setInterval(() => {
                        attempts++;
                        console.log(`Checking Three.js viewer initialization for ${buildingId}, attempt ${attempts}, initialized: ${viewer.isInitialized}`);
                        
                        if (viewer.isInitialized) {
                            clearInterval(checkInitialized);
                            
                            console.log(`Three.js viewer initialized for ${buildingId}, loading building...`);
                            
                            // Find and update loading message (don't clear container - it has the canvas!)
                            let loadingMsg = viewerEl.querySelector(`#loading-msg-${containerId}`);
                            if (!loadingMsg) {
                                loadingMsg = viewerEl.querySelector('div[style*="padding: 20px"]');
                            }
                            if (loadingMsg) {
                                loadingMsg.textContent = 'Loading building...';
                            }
                            
                            // Load the building
                            try {
                                console.log(`Calling loadBuilding for ${buildingId}`);
                                viewer.loadBuilding(minimalCityJSON);
                                
                                // Wait a moment for rendering
                                setTimeout(() => {
                                    // Update ID display
                                    if (idEl) {
                                        idEl.textContent = `${viewerType === 'candidate' ? 'Candidate' : 'Index'}: ${buildingId}`;
                                    }
                                    
                                    // Remove loading message (but keep the canvas!)
                                    if (loadingMsg && loadingMsg.parentNode) {
                                        loadingMsg.parentNode.removeChild(loadingMsg);
                                    }
                                    
                                    console.log(`Successfully loaded building ${buildingId} in Three.js viewer`);
                                    
                                    // Call completion callback
                                    if (onComplete) {
                                        setTimeout(onComplete, 100);
                                    }
                                }, 500); // Increased delay to ensure building is rendered
                            } catch (loadError) {
                                console.error(`Error loading building in Three.js viewer:`, loadError);
                                console.error('Error stack:', loadError.stack);
                                if (loadingMsg) {
                                    loadingMsg.textContent = `Error: ${loadError.message}`;
                                    loadingMsg.style.color = '#dc3545';
                                } else {
                                    const errorDiv = document.createElement('div');
                                    errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10;';
                                    errorDiv.textContent = `Error: ${loadError.message}`;
                                    viewerEl.appendChild(errorDiv);
                                }
                                if (onComplete) onComplete();
                            }
                        } else if (attempts >= maxAttempts) {
                            clearInterval(checkInitialized);
                            console.error(`Three.js viewer initialization timeout for ${containerId}`);
                            let loadingMsg = viewerEl.querySelector(`#loading-msg-${containerId}`);
                            if (loadingMsg) {
                                loadingMsg.textContent = 'Viewer initialization timeout. Check console for errors.';
                                loadingMsg.style.color = '#dc3545';
                            } else {
                                const errorDiv = document.createElement('div');
                                errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10;';
                                errorDiv.textContent = 'Viewer initialization timeout. Check console for errors.';
                                viewerEl.appendChild(errorDiv);
                            }
                            if (onComplete) onComplete();
                        }
                    }, 100);
                } catch (error) {
                    console.error(`Error creating Three.js viewer for ${containerId}:`, error);
                    console.error('Error stack:', error.stack);
                    const errorDiv = document.createElement('div');
                    errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10;';
                    errorDiv.textContent = `Error: ${error.message}`;
                    viewerEl.appendChild(errorDiv);
                    if (onComplete) onComplete();
                }
            }, 100);
            };
            
            // Start checking for Three.js
            checkThreeJS();
        })
        .catch(error => {
            console.error(`Error loading single building ${buildingId} from ${filePath}:`, error);
            viewerEl.innerHTML = `<div style="padding: 20px; text-align: center; color: #dc3545;">Error: ${error.message}</div>`;
            if (onComplete) onComplete();
        });
}


// Clean up comparison viewer instances
function cleanupComparisonViewers() {
    console.log('Cleaning up old comparison viewer instances');
    
    // Dispose candidate viewer
    if (window['comparison-viewer-candidate']) {
        try {
            const viewer = window['comparison-viewer-candidate'];
            if (viewer.dispose) {
                viewer.dispose();
            }
            delete window['comparison-viewer-candidate'];
        } catch (e) {
            console.warn('Error disposing candidate viewer:', e);
        }
    }
    
    // Dispose pair viewers (both old 'pair' key and new 'pair-{index}' keys)
    for (let i = 0; i < 10; i++) {
        const viewerKey = `comparison-viewer-pair-${i}`;
        if (window[viewerKey]) {
            try {
                const viewer = window[viewerKey];
                if (viewer.dispose) {
                    viewer.dispose();
                }
                delete window[viewerKey];
            } catch (e) {
                console.warn(`Error disposing pair viewer ${i}:`, e);
            }
        }
    }
    
    // Also try to dispose any viewer stored with 'pair' key (old format)
    if (window['comparison-viewer-pair']) {
        try {
            const viewer = window['comparison-viewer-pair'];
            if (viewer.dispose) {
                viewer.dispose();
            }
            delete window['comparison-viewer-pair'];
        } catch (e) {
            console.warn('Error disposing pair viewer:', e);
        }
    }
    
    // Clean up any other comparison viewer keys
    Object.keys(window).forEach(key => {
        if (key.startsWith('comparison-viewer-') && window[key] && typeof window[key] === 'object' && window[key].dispose) {
            try {
                window[key].dispose();
                delete window[key];
            } catch (e) {
                console.warn(`Error disposing viewer ${key}:`, e);
            }
        }
    });
}

// Show classifier results in comparison window
function showClassifierResultsInComparisonWindow(candidateBuildingId, pairs) {
    console.log('Showing classifier results for building:', candidateBuildingId);
    
    const classifierSection = document.getElementById('comparison-classifier-section');
    const classifierResults = document.getElementById('classifier-results');
    const showClassifierBtn = document.getElementById('show-classifier-results-btn');
    
    if (!classifierSection || !classifierResults) {
        console.error('Classifier section elements not found');
        return;
    }
    
    // Show the section (it's already visible, but ensure it's displayed)
    classifierSection.style.display = 'block';
    
    // Update button text to indicate results are shown (but keep it enabled so user can see results again)
    if (showClassifierBtn) {
        showClassifierBtn.textContent = 'Classifier Results (shown)';
        // Don't disable the button - allow user to scroll to results again if needed
    }
    
    // Clear previous results
    classifierResults.innerHTML = '';
    
    // Analyze pairs to determine summary message
    let hasMatchPrediction = false;
    let hasTrueMatch = false;
    let hasFalsePositive = false;
    
    pairs.forEach((pair) => {
        const prediction = pair.prediction !== undefined ? pair.prediction : null;
        const trueLabel = pair.true_label !== undefined && pair.true_label !== null ? pair.true_label : null;
        
        if (prediction === 1) {
            hasMatchPrediction = true;
            if (trueLabel === 1) {
                hasTrueMatch = true;
            } else if (trueLabel === 0) {
                hasFalsePositive = true;
            }
        }
    });
    
    // Create results container
    const resultsContainer = document.createElement('div');
    resultsContainer.style.cssText = 'background: #f8f9fa; border-radius: 8px; padding: 15px;';
    
    // Add heading
    const heading = document.createElement('h4');
    heading.style.cssText = 'margin: 0 0 15px 0; color: #667eea; font-size: 16px;';
    heading.textContent = 'Classifier Predictions & True Labels';
    resultsContainer.appendChild(heading);
    
    // Add summary message
    const summaryDiv = document.createElement('div');
    summaryDiv.style.cssText = 'background: white; border: 2px solid #667eea; border-radius: 6px; padding: 12px; margin-bottom: 15px; font-weight: 600;';
    
    if (!hasMatchPrediction) {
        summaryDiv.style.color = '#6c757d';
        summaryDiv.textContent = 'No matches were found in the BKAFI pairs';
    } else if (hasTrueMatch) {
        summaryDiv.style.color = '#28a745';
        summaryDiv.textContent = 'True match was found';
    } else if (hasFalsePositive) {
        summaryDiv.style.color = '#dc3545';
        summaryDiv.textContent = 'False positive match was found';
    } else {
        summaryDiv.style.color = '#6c757d';
        summaryDiv.textContent = 'Match predictions found (true labels unknown)';
    }
    
    resultsContainer.appendChild(summaryDiv);
    
    // Add results for each pair
    pairs.forEach((pair, index) => {
        const pairResult = document.createElement('div');
        pairResult.style.cssText = 'background: white; border: 1px solid #e0e0e0; border-radius: 6px; padding: 12px; margin-bottom: 10px;';
        
        const prediction = pair.prediction !== undefined ? pair.prediction : null;
        const trueLabel = pair.true_label !== undefined && pair.true_label !== null ? pair.true_label : null;
        
        // Determine colors based on values
        const predictionColor = prediction === 1 ? '#28a745' : '#dc3545';
        const predictionText = prediction === 1 ? 'Match' : 'No Match';
        const trueLabelColor = trueLabel === 1 ? '#28a745' : (trueLabel === 0 ? '#dc3545' : '#6c757d');
        const trueLabelText = trueLabel === 1 ? 'Match' : (trueLabel === 0 ? 'No Match' : 'Unknown');
        
        // Check if prediction matches true label
        const isCorrect = prediction !== null && trueLabel !== null && prediction === trueLabel;
        const correctnessBadge = isCorrect ? 
            '<span style="background: #28a745; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-left: 8px;">✓ Correct</span>' :
            (trueLabel !== null ? '<span style="background: #dc3545; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-left: 8px;">✗ Incorrect</span>' : '');
        
        pairResult.innerHTML = `
            <div style="margin-bottom: 8px;">
                <strong style="color: #333;">Pair ${index + 1}</strong>
                ${correctnessBadge}
            </div>
            <div style="font-size: 13px; color: #666; margin-bottom: 6px;">
                <strong>Index Building ID:</strong> ${pair.index_id}
            </div>
            <div style="display: flex; gap: 15px; margin-top: 10px;">
                <div style="flex: 1;">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">Prediction</div>
                    <div style="font-size: 14px; font-weight: 600; color: ${predictionColor};">
                        ${predictionText}
                    </div>
                </div>
                <div style="flex: 1;">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">True Label</div>
                    <div style="font-size: 14px; font-weight: 600; color: ${trueLabelColor};">
                        ${trueLabelText}
                    </div>
                </div>
            </div>
        `;
        
        resultsContainer.appendChild(pairResult);
    });
    
    classifierResults.appendChild(resultsContainer);
    
    console.log('Classifier results displayed for', pairs.length, 'pairs');
    
    // Auto-scroll to show the classifier results after a short delay to ensure DOM is updated
    setTimeout(() => {
        const comparisonContent = document.querySelector('.comparison-content');
        if (comparisonContent && classifierSection) {
            // Scroll to the classifier section smoothly
            classifierSection.scrollIntoView({ 
                behavior: 'smooth', 
                block: 'start',
                inline: 'nearest'
            });
            console.log('Scrolled to classifier results');
        }
    }, 100);
}

// Close BKAFI comparison window
function closeBkafiComparisonWindow() {
    const comparisonWindow = document.getElementById('bkafi-comparison-window');
    const overlay = document.getElementById('comparison-overlay');
    
    if (comparisonWindow) {
        comparisonWindow.style.display = 'none';
    }
    
    if (overlay) {
        overlay.classList.remove('active');
    }
    
    // Clean up viewers when closing
    cleanupComparisonViewers();
    
    // Clear viewer elements
    const candidateViewerEl = document.getElementById('comparison-viewer-candidate');
    const pairsViewersEl = document.getElementById('comparison-pairs-viewers');
    
    if (candidateViewerEl) {
        candidateViewerEl.innerHTML = '';
    }
    if (pairsViewersEl) {
        pairsViewersEl.innerHTML = '';
    }
    
    // Reset classifier section
    const classifierSection = document.getElementById('comparison-classifier-section');
    const classifierResults = document.getElementById('classifier-results');
    const showClassifierBtn = document.getElementById('show-classifier-results-btn');
    
    if (classifierSection) {
        classifierSection.style.display = 'none';
    }
    if (classifierResults) {
        classifierResults.innerHTML = '';
    }
    if (showClassifierBtn) {
        showClassifierBtn.disabled = false;
        showClassifierBtn.textContent = 'Show Classifier Results';
    }
}

// View results (Step 3)
function viewResults() {
    if (!pipelineState.step2Completed) {
        alert('Please complete BKAFI Blocking first.');
        return;
    }
    
    console.log('Viewing results for building:', selectedBuildingId);
    
    // Load matches and show in matches window
    fetch(`/api/building/matches/${selectedBuildingId}?file=${encodeURIComponent(selectedFile)}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error loading matches:', data.error);
                alert('Error loading matches: ' + data.error);
                return;
            }
            
            // Mark step 3 as completed
            pipelineState.step3Completed = true;
            updatePipelineUI();
            
            // Show matches window
            showBuildingMatches(
                selectedBuildingId,
                selectedBuildingData?.attributes?.name || selectedBuildingId,
                data.matches || []
            );
        })
        .catch(error => {
            console.error('Error loading matches:', error);
            alert('Error loading matches: ' + error.message);
        });
}

// Update pipeline UI with status indicators
function updatePipelineUI() {
    // Update step 1
    const step1El = document.getElementById('step-1');
    const step1Status = step1El.querySelector('.step-status');
    if (pipelineState.step1Completed) {
        step1Status.innerHTML = '✓';
        step1Status.className = 'step-status completed';
    } else {
        step1Status.innerHTML = '';
        step1Status.className = 'step-status';
    }
    
    // Update step 2
    const step2El = document.getElementById('step-2');
    const step2Status = step2El.querySelector('.step-status');
    if (pipelineState.step2Completed) {
        step2Status.innerHTML = '✓';
        step2Status.className = 'step-status completed';
    } else {
        step2Status.innerHTML = '';
        step2Status.className = 'step-status';
    }
    
    // Update step 3
    const step3El = document.getElementById('step-3');
    const step3Status = step3El.querySelector('.step-status');
    if (pipelineState.step3Completed) {
        step3Status.innerHTML = '✓';
        step3Status.className = 'step-status completed';
    } else {
        step3Status.innerHTML = '';
        step3Status.className = 'step-status';
    }
}

// Viewer controls
function resetCamera() {
    console.log('Resetting camera');
    if (window.viewer && window.viewer.resetCamera) {
        window.viewer.resetCamera();
    }
}

function toggleFullscreen() {
    console.log('Toggling fullscreen');
    if (window.viewer && window.viewer.toggleFullscreen) {
        window.viewer.toggleFullscreen();
    }
}

// Show building matches window
function showBuildingMatches(buildingId, buildingName, matches) {
    const matchesWindow = document.getElementById('matches-window');
    const buildingNameEl = document.getElementById('building-name');
    const buildingIdEl = document.getElementById('building-id');
    const matchesList = document.getElementById('matches-list');
    
    if (!matchesWindow || !buildingNameEl || !buildingIdEl || !matchesList) {
        console.error('Matches window elements not found');
        return;
    }
    
    // Update building info
    buildingNameEl.textContent = buildingName || 'Building';
    buildingIdEl.textContent = `ID: ${buildingId}`;
    
    // Clear and populate matches
    matchesList.innerHTML = '';
    
    if (matches && matches.length > 0) {
        matches.forEach((match, index) => {
            const matchItem = document.createElement('div');
            matchItem.className = 'match-item';
            matchItem.innerHTML = `
                <div class="match-header">
                    <span class="match-source">${match.source || 'Source'}</span>
                    <span class="match-confidence">${((match.confidence || match.similarity || 0) * 100).toFixed(1)}%</span>
                </div>
                <div class="match-details">
                    <p><strong>ID:</strong> ${match.id || match.building_id || 'N/A'}</p>
                    ${match.similarity ? `<p><strong>Similarity:</strong> ${(match.similarity * 100).toFixed(1)}%</p>` : ''}
                    ${match.features ? `<p><strong>Features:</strong> ${match.features}</p>` : ''}
                    <button onclick="viewMatch('${match.id || match.building_id || ''}')">View in 3D</button>
                </div>
            `;
            matchesList.appendChild(matchItem);
        });
    } else {
        matchesList.innerHTML = '<p style="text-align: center; color: #666; padding: 20px;">No matches found for this building</p>';
    }
    
    // Show the window
    matchesWindow.style.display = 'block';
    
    // Add overlay (optional, for better UX)
    let overlay = document.getElementById('matches-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'matches-overlay';
        overlay.className = 'matches-overlay';
        overlay.onclick = closeMatchesWindow;
        document.body.appendChild(overlay);
    }
    overlay.classList.add('active');
}

// Close matches window
function closeMatchesWindow() {
    const matchesWindow = document.getElementById('matches-window');
    const overlay = document.getElementById('matches-overlay');
    
    if (matchesWindow) {
        matchesWindow.style.display = 'none';
    }
    
    if (overlay) {
        overlay.classList.remove('active');
    }
}

// View a specific match in 3D
function viewMatch(matchId) {
    console.log('Viewing match:', matchId);
    // Close matches window
    closeMatchesWindow();
    
    // TODO: Implement logic to highlight/zoom to the matched building
    // This would require loading the matched building's data and highlighting it
    if (window.viewer) {
        // You can add logic here to highlight the matched building
        alert(`Viewing match: ${matchId}\n(This feature can be extended to highlight the matched building in the 3D viewer)`);
    }
}

// Make functions globally available
window.showBuildingMatches = showBuildingMatches;
window.closeMatchesWindow = closeMatchesWindow;
window.viewMatch = viewMatch;
window.showBuildingProperties = showBuildingProperties;
window.closeBuildingProperties = closeBuildingProperties;