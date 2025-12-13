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
    
    // Insert at the beginning (newest content at top)
    propsListEl.insertBefore(featuresContainer, propsListEl.firstChild);
    
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
            
            // Show pairs in properties window
            showBkafiPairs(data.pairs);
        })
        .catch(error => {
            console.error('Error loading BKAFI pairs:', error);
        });
}

// Show BKAFI pairs in properties window
function showBkafiPairs(pairs) {
    const propsListEl = document.getElementById('properties-list');
    if (!propsListEl || !pairs || pairs.length === 0) return;
    
    // Remove existing BKAFI section if it exists
    const existingBkafiSection = propsListEl.querySelector('.bkafi-pairs-section');
    if (existingBkafiSection) {
        existingBkafiSection.remove();
    }
    
    // Create container for BKAFI pairs
    const bkafiContainer = document.createElement('div');
    bkafiContainer.className = 'bkafi-pairs-section';
    
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
    
    // Insert at the beginning (newest content at top)
    propsListEl.insertBefore(bkafiContainer, propsListEl.firstChild);
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