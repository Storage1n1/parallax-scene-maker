// Parallax Scene Stage — exposes window.ParallaxScene, driven from Playwright.
//
// Coordinate system: x,y are normalized screen-space in [-1, 1] (0,0 = center,
// 1 = right/top edge). z is world-space depth; layers have fixed default z
// so "depth" is a first-class concept rather than something the VLM has to
// reason about in raw units.

(function () {
  const CANVAS_W = 1280, CANVAS_H = 720;

  const LAYER_DEPTH = {
    background: -900,
    midground: -450,
    foreground: -150,
    subject: 0,
  };
  const LAYER_ORDER = ["background", "midground", "foreground", "subject"];

  let scene, camera, renderer, root;
  let elements = {};      // id -> {mesh, layer, xNorm, yNorm, scale, aspect}
  let textEls = {};       // id -> DOM node + params
  let fogDefault = null;
  let baseCameraFov = 50;

  function init() {
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(baseCameraFov, CANVAS_W / CANVAS_H, 1, 3000);
    camera.position.set(0, 0, 600);
    camera.lookAt(0, 0, 0);

    renderer = new THREE.WebGLRenderer({ canvas: document.getElementById("stage"), antialias: true, preserveDrawingBuffer: true });
    renderer.setSize(CANVAS_W, CANVAS_H);
    renderer.setClearColor(0x10141c, 1);

    const ambient = new THREE.AmbientLight(0xffffff, 1.0);
    scene.add(ambient);

    root = document.getElementById("textLayer");
    render();
    window.__sceneReady = true;
  }

  function render() {
    renderer.render(scene, camera);
  }

  // Visible world width/height of the camera frustum at a given depth z
  // (z is negative going into the screen; camera sits at cameraZ).
  function visibleSizeAtDepth(z) {
    const distance = camera.position.z - z;
    const vFov = (camera.fov * Math.PI) / 180;
    const height = 2 * Math.tan(vFov / 2) * distance;
    const width = height * camera.aspect;
    return { width, height };
  }

  function normToWorld(xNorm, yNorm, z) {
    const { width, height } = visibleSizeAtDepth(z);
    return { x: (xNorm * width) / 2, y: (yNorm * height) / 2 };
  }

  function loadTexture(path) {
    return new Promise((resolve, reject) => {
      new THREE.TextureLoader().load(
        path,
        (tex) => {
          tex.colorSpace = THREE.SRGBColorSpace;
          resolve(tex);
        },
        undefined,
        (err) => reject(err)
      );
    });
  }

  // scaleFrac: fraction of that depth's visible HEIGHT the element's height should occupy.
  async function addElement(id, layer, imagePath, opts) {
    opts = opts || {};
    const z = LAYER_DEPTH.hasOwnProperty(layer) ? LAYER_DEPTH[layer] : LAYER_DEPTH.midground;
    const tex = await loadTexture(imagePath);
    const aspect = tex.image.width / tex.image.height;

    const scaleFrac = opts.scaleFrac !== undefined ? opts.scaleFrac : (layer === "background" ? 1.05 : 0.5);
    const { height: visH } = visibleSizeAtDepth(z);
    const planeH = visH * scaleFrac;
    const planeW = planeH * aspect;

    const geo = new THREE.PlaneGeometry(planeW, planeH);
    const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false });
    const mesh = new THREE.Mesh(geo, mat);

    const xNorm = opts.x !== undefined ? opts.x : 0;
    const yNorm = opts.y !== undefined ? opts.y : (layer === "background" ? 0 : -0.15);
    const world = normToWorld(xNorm, yNorm, z);
    mesh.position.set(world.x, world.y, z);
    mesh.renderOrder = LAYER_ORDER.indexOf(layer);

    scene.add(mesh);
    elements[id] = { mesh, layer, xNorm, yNorm, scaleFrac, aspect, z };
    render();
    return { id, layer, x: xNorm, y: yNorm, z, scaleFrac };
  }

  function moveElement(id, delta) {
    const el = elements[id];
    if (!el) return null;
    delta = delta || {};
    if (delta.dx !== undefined) el.xNorm += delta.dx;
    if (delta.dy !== undefined) el.yNorm += delta.dy;
    if (delta.dScale !== undefined) el.scaleFrac = Math.max(0.02, el.scaleFrac + delta.dScale);
    if (delta.layer !== undefined && LAYER_DEPTH.hasOwnProperty(delta.layer)) {
      el.layer = delta.layer;
      el.z = LAYER_DEPTH[delta.layer];
    }
    applyElementTransform(id);
    render();
    return getElementState(id);
  }

  function setElement(id, abs) {
    const el = elements[id];
    if (!el) return null;
    abs = abs || {};
    if (abs.x !== undefined) el.xNorm = abs.x;
    if (abs.y !== undefined) el.yNorm = abs.y;
    if (abs.scaleFrac !== undefined) el.scaleFrac = abs.scaleFrac;
    if (abs.layer !== undefined && LAYER_DEPTH.hasOwnProperty(abs.layer)) {
      el.layer = abs.layer;
      el.z = LAYER_DEPTH[abs.layer];
    }
    applyElementTransform(id);
    render();
    return getElementState(id);
  }

  function applyElementTransform(id) {
    const el = elements[id];
    const world = normToWorld(el.xNorm, el.yNorm, el.z);
    const { height: visH } = visibleSizeAtDepth(el.z);
    const planeH = visH * el.scaleFrac;
    const planeW = planeH * el.aspect;
    el.mesh.geometry.dispose();
    el.mesh.geometry = new THREE.PlaneGeometry(planeW, planeH);
    el.mesh.position.set(world.x, world.y, el.z);
    el.mesh.renderOrder = LAYER_ORDER.indexOf(el.layer);
  }

  function removeElement(id) {
    const el = elements[id];
    if (!el) return false;
    scene.remove(el.mesh);
    el.mesh.geometry.dispose();
    el.mesh.material.dispose();
    delete elements[id];
    render();
    return true;
  }

  function getElementState(id) {
    const el = elements[id];
    if (!el) return null;
    return { id, layer: el.layer, x: round2(el.xNorm), y: round2(el.yNorm), scaleFrac: round2(el.scaleFrac) };
  }

  function listElements() {
    return Object.keys(elements).map(getElementState);
  }

  function round2(n) { return Math.round(n * 100) / 100; }

  function moveCamera(delta) {
    delta = delta || {};
    if (delta.dx !== undefined) camera.position.x += delta.dx;
    if (delta.dy !== undefined) camera.position.y += delta.dy;
    if (delta.dz !== undefined) camera.position.z += delta.dz;
    if (delta.dFov !== undefined) { camera.fov = Math.max(20, Math.min(90, camera.fov + delta.dFov)); camera.updateProjectionMatrix(); }
    camera.lookAt(0, 0, 0);
    render();
    return getCameraState();
  }

  function setCamera(abs) {
    abs = abs || {};
    if (abs.x !== undefined) camera.position.x = abs.x;
    if (abs.y !== undefined) camera.position.y = abs.y;
    if (abs.z !== undefined) camera.position.z = abs.z;
    if (abs.fov !== undefined) { camera.fov = abs.fov; camera.updateProjectionMatrix(); }
    camera.lookAt(0, 0, 0);
    render();
    return getCameraState();
  }

  function getCameraState() {
    return { x: round2(camera.position.x), y: round2(camera.position.y), z: round2(camera.position.z), fov: round2(camera.fov) };
  }

  function setAtmosphere(opts) {
    opts = opts || {};
    if (opts.fog) {
      const c = new THREE.Color(opts.fog.color || "#10141c");
      scene.fog = new THREE.Fog(c, opts.fog.near !== undefined ? opts.fog.near : 200, opts.fog.far !== undefined ? opts.fog.far : 1500);
    } else if (opts.fog === null) {
      scene.fog = null;
    }
    if (opts.background) {
      renderer.setClearColor(new THREE.Color(opts.background), 1);
    }
    render();
    return true;
  }

  function setText(id, opts) {
    opts = opts || {};
    let node = textEls[id];
    if (!node) {
      node = document.createElement("div");
      node.className = "caption";
      root.appendChild(node);
      textEls[id] = node;
    }
    if (opts.remove) {
      node.remove();
      delete textEls[id];
      return true;
    }
    node.textContent = opts.content !== undefined ? opts.content : node.textContent;
    const xPct = opts.x !== undefined ? (opts.x + 1) / 2 * 100 : 50;
    const yPct = opts.y !== undefined ? (1 - (opts.y + 1) / 2) * 100 : 85;
    node.style.left = xPct + "%";
    node.style.top = yPct + "%";
    node.style.transform = "translate(-50%, -50%)";
    node.style.color = opts.color || "#ffffff";
    node.style.fontSize = (opts.size || 48) + "px";
    if (opts.font) node.style.fontFamily = opts.font;
    return true;
  }

  function getState() {
    return {
      camera: getCameraState(),
      elements: listElements(),
      fog: scene.fog ? { color: "#" + scene.fog.color.getHexString(), near: scene.fog.near, far: scene.fog.far } : null,
    };
  }

  window.ParallaxScene = {
    LAYER_DEPTH, LAYER_ORDER,
    addElement, moveElement, setElement, removeElement,
    moveCamera, setCamera,
    setAtmosphere, setText,
    listElements, getState,
    render,
  };

  init();
})();
