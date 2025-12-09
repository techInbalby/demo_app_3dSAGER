# 3dSAGER Demo - Professional Pipeline Visualization

A professional web application for demonstrating the 3dSAGER (3D Spatial-Aware Geospatial Entity Resolution) pipeline capabilities.

## 🚀 Features

- **Professional Homepage**: Modern, responsive design showcasing the 3dSAGER pipeline
- **Interactive Demo**: Full-featured demonstration interface with CityJson file upload and processing simulation
- **3D Visualization**: Ready for Three.js integration for 3D mesh rendering
- **Entity Resolution**: Complete geospatial entity resolution workflow demonstration
- **BKAFI Blocking**: Demonstration of feature importance-based blocking
- **Export Functionality**: Download results and analysis data
- **Docker Support**: Containerized deployment with Nginx reverse proxy

## 🏗️ Architecture

### Technology Stack
- **Backend**: Flask (Python) - Lightweight, flexible web framework
- **Frontend**: Vanilla JavaScript, HTML5, CSS3 - No framework dependencies
- **3D Rendering**: Three.js (ready for integration)
- **Deployment**: Docker + Docker Compose + Nginx
- **File Processing**: Support for .json, .jsonl, .ply, .obj formats

### Why Flask over Vue.js?

**Flask Advantages for Academic Demos:**
- ✅ **Real Pipeline Integration**: Can run actual ML models and Python libraries
- ✅ **File Processing**: Handle real 3D data uploads and processing
- ✅ **Research Ready**: Easy integration with PyTorch, TensorFlow, NumPy
- ✅ **Simple Deployment**: Single container, easy to share and demonstrate
- ✅ **Academic Friendly**: Familiar to researchers, easy to extend

**Vue.js Limitations for This Use Case:**
- ❌ **Frontend Only**: Cannot run actual ML pipeline processing
- ❌ **No Backend**: Limited to mock data and simulations
- ❌ **Complex Deployment**: Requires separate frontend/backend setup

## 📁 Project Structure

```
demo_3dSAGER/
├── app.py                 # Flask application
├── requirements.txt       # Python dependencies
├── Dockerfile            # Container configuration
├── docker-compose.yml    # Multi-container setup
├── nginx.conf           # Reverse proxy configuration
├── templates/           # HTML templates
│   ├── index.html       # Homepage
│   └── demo.html        # Demo page
├── static/              # Static assets
│   ├── css/            # Stylesheets
│   ├── js/             # JavaScript
│   └── images/         # Images and assets
└── uploads/            # File upload directory
```

## 🚀 Quick Start

### Option 1: Docker (Recommended)

```bash
# Build and run with Docker Compose
docker-compose up --build

# Access the application
open http://localhost
```

### Option 2: Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py

# Access the application
open http://localhost:5000
```

## 🔧 Development

### Adding Real Pipeline Integration

To integrate your actual 3dSAGER pipeline:

1. **Replace Mock Functions** in `app.py`:
   ```python
   @app.route('/api/pipeline/run', methods=['POST'])
   def run_pipeline():
       # Replace with actual pipeline execution
       from your_pipeline import run_3dsager_pipeline
       results = run_3dsager_pipeline(file_path)
       return jsonify(results)
   ```

2. **Add ML Dependencies** to `requirements.txt`:
   ```
   torch>=1.9.0
   torchvision>=0.10.0
   numpy>=1.21.0
   scipy>=1.7.0
   ```

3. **Integrate 3D Viewer**: Add Three.js components to `static/js/demo.js`

### Customization

- **Styling**: Modify `static/css/style.css` and `static/css/demo.css`
- **Templates**: Update `templates/index.html` and `templates/demo.html`
- **API Endpoints**: Extend `app.py` with additional routes
- **3D Visualization**: Integrate Three.js in `static/js/demo.js`

## 📊 API Endpoints

- `GET /` - Homepage
- `GET /demo` - Demo interface
- `POST /api/upload` - File upload
- `POST /api/pipeline/run` - Execute pipeline
- `GET /api/pipeline/status/<file_id>` - Check status
- `GET /api/results/<file_id>` - Get results
- `GET /api/export/<file_id>` - Download results
- `GET /api/health` - Health check

## 🐳 Docker Deployment

### Production Deployment

```bash
# Build production image
docker build -t 3dsager-demo .

# Run with production settings
docker run -d -p 80:80 -p 443:443 3dsager-demo
```

### Development with Hot Reload

```bash
# Run in development mode
docker-compose -f docker-compose.dev.yml up
```

## 🎯 Academic Use

This demo is designed for:
- **Research Presentations**: Professional appearance for academic conferences
- **Paper Demonstrations**: Showcase 3dSAGER capabilities
- **Collaboration**: Easy to share and deploy
- **Extension**: Simple to add real pipeline integration

## 📝 License

MIT License - Built for academic research and demonstration purposes.

## 🤝 Contributing

This is a demonstration application for the 3dSAGER research project. For contributions to the core research, please refer to the main 3dSAGER repository.

---

**Built for the 3dSAGER Research Team**  
*Professional 3D Scene Analysis and Generation Pipeline*
