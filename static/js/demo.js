// 3dSAGER Demo JavaScript
let currentSource = 'A';
let currentSessionId = null;

// Initialize when page loads
document.addEventListener('DOMContentLoaded', function() {
    console.log('3dSAGER Demo initialized');
    loadDataFiles();
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

// Load file in 3D viewer
function loadFileInViewer(filePath) {
    console.log('Loading file in viewer:', filePath);
    console.log('Viewer available:', !!window.viewer);
    
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
        }, 500);
    } else {
        console.error('CityJSON viewer not available');
        const viewer = document.getElementById('viewer');
        viewer.innerHTML = `
            <div class="placeholder">
                <div class="placeholder-icon">⚠️</div>
                <p>3D Viewer not ready. Please refresh the page.</p>
            </div>
        `;
    }
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