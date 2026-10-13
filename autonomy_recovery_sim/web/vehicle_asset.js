(function registerVehicleAssets(global) {
  "use strict";

  const GLB_MAGIC = 0x46546c67;
  const JSON_CHUNK = 0x4e4f534a;
  const BIN_CHUNK = 0x004e4942;
  const componentReaders = {
    5121: { bytes: 1, read: "getUint8" },
    5123: { bytes: 2, read: "getUint16" },
    5125: { bytes: 4, read: "getUint32" },
    5126: { bytes: 4, read: "getFloat32" },
  };
  const typeSizes = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };

  function parseGlb(buffer) {
    const view = new DataView(buffer);
    if (view.getUint32(0, true) !== GLB_MAGIC || view.getUint32(4, true) !== 2) {
      throw new Error("Das Fahrzeug-Asset ist kein glTF-2.0-Binary.");
    }
    const chunks = new Map();
    let offset = 12;
    while (offset < buffer.byteLength) {
      const length = view.getUint32(offset, true);
      const type = view.getUint32(offset + 4, true);
      chunks.set(type, { offset: offset + 8, length });
      offset += 8 + length;
    }
    const jsonChunk = chunks.get(JSON_CHUNK);
    const binaryChunk = chunks.get(BIN_CHUNK);
    if (!jsonChunk || !binaryChunk) throw new Error("JSON- oder BIN-Chunk fehlt im Fahrzeug-Asset.");
    const jsonBytes = new Uint8Array(buffer, jsonChunk.offset, jsonChunk.length);
    const gltf = JSON.parse(new TextDecoder().decode(jsonBytes).replace(/[\u0000 ]+$/, ""));
    return { buffer, gltf, binaryChunk };
  }

  function readAccessor(document, accessorIndex) {
    const { buffer, gltf, binaryChunk } = document;
    const accessor = gltf.accessors[accessorIndex];
    const bufferView = gltf.bufferViews[accessor.bufferView];
    const component = componentReaders[accessor.componentType];
    const componentCount = typeSizes[accessor.type];
    if (!component || !componentCount) throw new Error(`Nicht unterstützter glTF-Accessor ${accessorIndex}.`);
    const stride = bufferView.byteStride || component.bytes * componentCount;
    const start = binaryChunk.offset + (bufferView.byteOffset || 0) + (accessor.byteOffset || 0);
    const source = new DataView(buffer);
    const values = new Float32Array(accessor.count * componentCount);
    for (let item = 0; item < accessor.count; item += 1) {
      for (let part = 0; part < componentCount; part += 1) {
        const byteOffset = start + item * stride + part * component.bytes;
        values[item * componentCount + part] = source[component.read](byteOffset, true);
      }
    }
    return values;
  }

  async function decodeTexture(document, primitive) {
    const material = document.gltf.materials?.[primitive.material];
    const textureIndex = material?.pbrMetallicRoughness?.baseColorTexture?.index;
    const imageIndex = document.gltf.textures?.[textureIndex]?.source;
    const image = document.gltf.images?.[imageIndex];
    if (!image || image.bufferView == null) return null;
    const imageView = document.gltf.bufferViews[image.bufferView];
    const start = document.binaryChunk.offset + (imageView.byteOffset || 0);
    const bytes = new Uint8Array(document.buffer, start, imageView.byteLength);
    const blob = new Blob([bytes], { type: image.mimeType || "image/png" });
    if ("createImageBitmap" in global) return global.createImageBitmap(blob);
    return new Promise((resolve, reject) => {
      const source = URL.createObjectURL(blob);
      const element = new Image();
      element.onload = () => {
        URL.revokeObjectURL(source);
        resolve(element);
      };
      element.onerror = () => {
        URL.revokeObjectURL(source);
        reject(new Error("Fahrzeugtextur konnte nicht dekodiert werden."));
      };
      element.src = source;
    });
  }

  async function load(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`Fahrzeug-Asset konnte nicht geladen werden (${response.status}).`);
    const document = parseGlb(await response.arrayBuffer());
    const meshNode = document.gltf.nodes.find((node) => node.mesh != null);
    const primitive = document.gltf.meshes?.[meshNode?.mesh]?.primitives?.[0];
    if (!primitive || primitive.mode != null && primitive.mode !== 4) {
      throw new Error("Das Fahrzeug-Asset enthält kein unterstütztes Dreiecksnetz.");
    }
    const positions = readAccessor(document, primitive.attributes.POSITION);
    const normals = primitive.attributes.NORMAL == null ? null : readAccessor(document, primitive.attributes.NORMAL);
    const texcoords = primitive.attributes.TEXCOORD_0 == null ? null : readAccessor(document, primitive.attributes.TEXCOORD_0);
    const indices = primitive.indices == null
      ? Float32Array.from({ length: positions.length / 3 }, (_, index) => index)
      : readAccessor(document, primitive.indices);
    return {
      positions,
      normals,
      texcoords,
      indices,
      texture: await decodeTexture(document, primitive),
      dimensions: document.gltf.asset?.extras?.autonomy_recovery_sim_dimensions_m || null,
    };
  }

  function modelToWorld(vehicle, x, y, z) {
    const cosYaw = Math.cos(vehicle.yaw_rad);
    const sinYaw = Math.sin(vehicle.yaw_rad);
    return {
      x: vehicle.x_m + z * cosYaw + x * sinYaw,
      y: vehicle.y_m + z * sinYaw - x * cosYaw,
      z: y,
    };
  }

  function trianglePath(context, points) {
    context.beginPath();
    context.moveTo(points[0].x, points[0].y);
    context.lineTo(points[1].x, points[1].y);
    context.lineTo(points[2].x, points[2].y);
    context.closePath();
  }

  function drawMappedTexture(context, texture, points, uv) {
    const source = uv.map((point) => ({ x: point[0] * texture.width, y: point[1] * texture.height }));
    const denominator = source[0].x * (source[1].y - source[2].y)
      + source[1].x * (source[2].y - source[0].y)
      + source[2].x * (source[0].y - source[1].y);
    if (Math.abs(denominator) < 1e-5) return false;
    const a = (points[0].x * (source[1].y - source[2].y)
      + points[1].x * (source[2].y - source[0].y)
      + points[2].x * (source[0].y - source[1].y)) / denominator;
    const b = (points[0].y * (source[1].y - source[2].y)
      + points[1].y * (source[2].y - source[0].y)
      + points[2].y * (source[0].y - source[1].y)) / denominator;
    const c = (points[0].x * (source[2].x - source[1].x)
      + points[1].x * (source[0].x - source[2].x)
      + points[2].x * (source[1].x - source[0].x)) / denominator;
    const d = (points[0].y * (source[2].x - source[1].x)
      + points[1].y * (source[0].x - source[2].x)
      + points[2].y * (source[1].x - source[0].x)) / denominator;
    const e = (points[0].x * (source[1].x * source[2].y - source[2].x * source[1].y)
      + points[1].x * (source[2].x * source[0].y - source[0].x * source[2].y)
      + points[2].x * (source[0].x * source[1].y - source[1].x * source[0].y)) / denominator;
    const f = (points[0].y * (source[1].x * source[2].y - source[2].x * source[1].y)
      + points[1].y * (source[2].x * source[0].y - source[0].x * source[2].y)
      + points[2].y * (source[0].x * source[1].y - source[1].x * source[0].y)) / denominator;
    context.save();
    trianglePath(context, points);
    context.clip();
    context.transform(a, b, c, d, e, f);
    context.drawImage(texture, 0, 0);
    context.restore();
    return true;
  }

  function triangleShade(asset, indices) {
    if (!asset.normals) return 0.12;
    let x = 0;
    let y = 0;
    let z = 0;
    indices.forEach((index) => {
      x += asset.normals[index * 3];
      y += asset.normals[index * 3 + 1];
      z += asset.normals[index * 3 + 2];
    });
    const length = Math.max(.001, Math.hypot(x, y, z));
    const light = (x * -.35 + y * .85 + z * .38) / length;
    return Math.max(0, Math.min(.3, .18 - light * .12));
  }

  function drawTriangle(context, asset, triangle) {
    const area = (triangle.points[1].x - triangle.points[0].x) * (triangle.points[2].y - triangle.points[0].y)
      - (triangle.points[1].y - triangle.points[0].y) * (triangle.points[2].x - triangle.points[0].x);
    if (Math.abs(area) < .04) return;
    const mapped = asset.texture && asset.texcoords && drawMappedTexture(
      context,
      asset.texture,
      triangle.points,
      triangle.indices.map((index) => [asset.texcoords[index * 2], asset.texcoords[index * 2 + 1]]),
    );
    if (!mapped) {
      trianglePath(context, triangle.points);
      context.fillStyle = "#087f73";
      context.fill();
    }
    const shade = triangleShade(asset, triangle.indices);
    if (shade > .01) {
      trianglePath(context, triangle.points);
      context.fillStyle = `rgba(12, 20, 18, ${shade})`;
      context.fill();
    }
  }

  function triangles(asset, projected, depthOf) {
    const result = [];
    for (let offset = 0; offset + 2 < asset.indices.length; offset += 3) {
      const indices = [asset.indices[offset], asset.indices[offset + 1], asset.indices[offset + 2]];
      const points = indices.map((index) => projected[index]);
      if (points.some((point) => !point)) continue;
      result.push({
        indices,
        points,
        depth: points.reduce((sum, point) => sum + depthOf(point), 0) / 3,
      });
    }
    return result;
  }

  function drawPerspective(context, asset, vehicle, project) {
    const projected = [];
    for (let offset = 0; offset < asset.positions.length; offset += 3) {
      const world = modelToWorld(
        vehicle,
        asset.positions[offset],
        asset.positions[offset + 1],
        asset.positions[offset + 2],
      );
      projected.push(project(world.x, world.y, world.z));
    }
    const ordered = triangles(asset, projected, (point) => point.depth);
    ordered.sort((a, b) => b.depth - a.depth);
    ordered.forEach((triangle) => drawTriangle(context, asset, triangle));
    return ordered.length > 0;
  }

  function drawTopDown(context, asset, vehicle, transform) {
    const projected = [];
    for (let offset = 0; offset < asset.positions.length; offset += 3) {
      const world = modelToWorld(
        vehicle,
        asset.positions[offset],
        asset.positions[offset + 1],
        asset.positions[offset + 2],
      );
      const [x, y] = transform(world.x, world.y);
      projected.push({ x, y, height: world.z });
    }
    const ordered = triangles(asset, projected, (point) => point.height);
    ordered.sort((a, b) => a.depth - b.depth);
    ordered.forEach((triangle) => drawTriangle(context, asset, triangle));
    return ordered.length > 0;
  }

  global.AutonomyRecoveryVehicleAssets = { load, drawPerspective, drawTopDown };
}(window));
