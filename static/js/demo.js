// 3dSAGER Demo JavaScript
let currentSource = 'A';
let currentSessionId = null;
let locationMap = null;

// Initialize when page loads
document.addEventListener('DOMContentLoaded', function() {
    console.log('3dSAGER Demo initialized');
    loadDataFiles();
    initLocationMap();
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

// Initialize location map
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

// Load file in 3D viewer
function loadFileInViewer(filePath) {
    console.log('Loading file in viewer:', filePath);
    console.log('Viewer available:', !!window.viewer);
    console.log('Cesium available:', typeof Cesium !== 'undefined');
    
    // Wait for viewer to be ready (with retry)
    const tryLoad = (attempts = 0) => {
        if (window.viewer && window.viewer.loadCityJSON) {
            // Extract relative path for API
            const relativePath = filePath.replace('data/RawCitiesData/The Hague/', '');
            console.log('Using relative path:', relativePath);
            window.viewer.loadCityJSON(relativePath);
            
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

// Pipeline step functions
function runStep(stepNumber) {
    console.log(`Running pipeline step ${stepNumber}`);
    
    // Mock pipeline execution
    const stepButtons = document.querySelectorAll('.step-btn');
    stepButtons[stepNumber - 1].textContent = 'Running...';
    stepButtons[stepNumber - 1].disabled = true;
    
    setTimeout(() => {
        stepButtons[stepNumber - 1].textContent = 'Completed';
        stepButtons[stepNumber - 1].style.background = '#28a745';
    }, 2000);
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