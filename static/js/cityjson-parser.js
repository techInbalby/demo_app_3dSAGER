// Custom CityJSON Parser for Three.js
// Based on Three.js documentation and ninja-main implementation

class CityJSONParser {
    constructor() {
        this.vertices = [];
        this.cityObjects = {};
        this.transform = null;
        this.boundingBox = new THREE.Box3();
    }
    
    parse(cityJSON) {
        console.log('Parsing CityJSON:', cityJSON);
        
        // Extract vertices
        this.vertices = cityJSON.vertices || [];
        console.log('Vertices loaded:', this.vertices.length);
        
        // Extract transform
        this.transform = cityJSON.transform || null;
        
        // Extract CityObjects
        this.cityObjects = cityJSON.CityObjects || {};
        console.log('CityObjects loaded:', Object.keys(this.cityObjects).length);
        
        // Calculate bounding box
        this.calculateBoundingBox();
        
        return this;
    }
    
    calculateBoundingBox() {
        this.boundingBox.makeEmpty();
        
        // Apply transform to vertices if available
        let transformedVertices = this.vertices;
        if (this.transform) {
            transformedVertices = this.vertices.map(vertex => {
                const [x, y, z] = vertex;
                return [
                    x * this.transform.scale[0] + this.transform.translate[0],
                    y * this.transform.scale[1] + this.transform.translate[1],
                    z * this.transform.scale[2] + this.transform.translate[2]
                ];
            });
        }
        
        // Calculate bounding box
        transformedVertices.forEach(vertex => {
            this.boundingBox.expandByPoint(new THREE.Vector3(vertex[0], vertex[1], vertex[2]));
        });
        
        console.log('Bounding box calculated:', this.boundingBox);
    }
    
    createGeometry(cityObjectId, geometry) {
        const cityObject = this.cityObjects[cityObjectId];
        if (!cityObject || !geometry) return null;
        
        const objectType = cityObject.type;
        const material = this.createMaterialForObjectType(objectType);
        
        // Create geometry based on type
        let mesh = null;
        
        if (geometry.type === 'Solid') {
            mesh = this.createSolidGeometry(geometry, material);
        } else if (geometry.type === 'MultiSurface') {
            mesh = this.createMultiSurfaceGeometry(geometry, material);
        } else if (geometry.type === 'CompositeSurface') {
            mesh = this.createCompositeSurfaceGeometry(geometry, material);
        }
        
        if (mesh) {
            mesh.userData = {
                isCityObject: true,
                objectId: cityObjectId,
                objectType: objectType,
                geometry: geometry
            };
        }
        
        return mesh;
    }
    
    createSolidGeometry(geometry, material) {
        const boundaries = geometry.boundaries;
        if (!boundaries || boundaries.length === 0) return null;
        
        const geometry3d = new THREE.BufferGeometry();
        const vertices = [];
        const indices = [];
        const normals = [];
        
        let vertexIndex = 0;
        
        // Process each shell in the solid
        boundaries.forEach(shell => {
            shell.forEach(face => {
                face.forEach(ring => {
                    if (ring.length >= 3) {
                        // Triangulate the ring
                        const ringVertices = ring.map(vertexIdx => {
                            const vertex = this.vertices[vertexIdx];
                            return new THREE.Vector3(vertex[0], vertex[1], vertex[2]);
                        });
                        
                        // Apply transform if available
                        if (this.transform) {
                            ringVertices.forEach(vertex => {
                                vertex.x = vertex.x * this.transform.scale[0] + this.transform.translate[0];
                                vertex.y = vertex.y * this.transform.scale[1] + this.transform.translate[1];
                                vertex.z = vertex.z * this.transform.scale[2] + this.transform.translate[2];
                            });
                        }
                        
                        // Add vertices
                        ringVertices.forEach(vertex => {
                            vertices.push(vertex.x, vertex.y, vertex.z);
                        });
                        
                        // Triangulate (simple fan triangulation)
                        for (let i = 1; i < ring.length - 1; i++) {
                            indices.push(vertexIndex, vertexIndex + i, vertexIndex + i + 1);
                        }
                        
                        vertexIndex += ring.length;
                    }
                });
            });
        });
        
        if (vertices.length > 0) {
            geometry3d.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
            geometry3d.setIndex(indices);
            geometry3d.computeVertexNormals();
            
            return new THREE.Mesh(geometry3d, material);
        }
        
        return null;
    }
    
    createMultiSurfaceGeometry(geometry, material) {
        const boundaries = geometry.boundaries;
        if (!boundaries || boundaries.length === 0) return null;
        
        const geometry3d = new THREE.BufferGeometry();
        const vertices = [];
        const indices = [];
        
        let vertexIndex = 0;
        
        boundaries.forEach(surface => {
            surface.forEach(ring => {
                if (ring.length >= 3) {
                    const ringVertices = ring.map(vertexIdx => {
                        const vertex = this.vertices[vertexIdx];
                        return new THREE.Vector3(vertex[0], vertex[1], vertex[2]);
                    });
                    
                    // Apply transform if available
                    if (this.transform) {
                        ringVertices.forEach(vertex => {
                            vertex.x = vertex.x * this.transform.scale[0] + this.transform.translate[0];
                            vertex.y = vertex.y * this.transform.scale[1] + this.transform.translate[1];
                            vertex.z = vertex.z * this.transform.scale[2] + this.transform.translate[2];
                        });
                    }
                    
                    // Add vertices
                    ringVertices.forEach(vertex => {
                        vertices.push(vertex.x, vertex.y, vertex.z);
                    });
                    
                    // Triangulate
                    for (let i = 1; i < ring.length - 1; i++) {
                        indices.push(vertexIndex, vertexIndex + i, vertexIndex + i + 1);
                    }
                    
                    vertexIndex += ring.length;
                }
            });
        });
        
        if (vertices.length > 0) {
            geometry3d.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
            geometry3d.setIndex(indices);
            geometry3d.computeVertexNormals();
            
            return new THREE.Mesh(geometry3d, material);
        }
        
        return null;
    }
    
    createCompositeSurfaceGeometry(geometry, material) {
        // For CompositeSurface, we'll create a simple representation
        return this.createMultiSurfaceGeometry(geometry, material);
    }
    
    createMaterialForObjectType(objectType) {
        const colors = {
            'Building': 0x7497df,
            'BuildingPart': 0x7497df,
            'BuildingInstallation': 0x7497df,
            'Bridge': 0x999999,
            'BridgePart': 0x999999,
            'BridgeInstallation': 0x999999,
            'BridgeConstructionElement': 0x999999,
            'CityObjectGroup': 0xffffb3,
            'CityFurniture': 0xcc0000,
            'GenericCityObject': 0xcc0000,
            'LandUse': 0xffffb3,
            'PlantCover': 0x39ac39,
            'Railway': 0x000000,
            'Road': 0x999999,
            'SolitaryVegetationObject': 0x39ac39,
            'TINRelief': 0xffdb99,
            'TransportSquare': 0x999999,
            'Tunnel': 0x999999,
            'TunnelPart': 0x999999,
            'TunnelInstallation': 0x999999,
            'WaterBody': 0x4da6ff
        };
        
        const color = colors[objectType] || 0x888888;
        return new THREE.MeshLambertMaterial({ 
            color: color,
            side: THREE.DoubleSide
        });
    }
}
