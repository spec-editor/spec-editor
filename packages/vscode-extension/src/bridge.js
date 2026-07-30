// Spec Editor — VSCode WebView Bridge
(function () {
  var v;
  try {
    v = acquireVsCodeApi();
  } catch (e) {
    return;
  }
  // Make v accessible from code outside this IIFE
  window.__vscodeApi = v;
  // Default relation scope: internal only
  window.__SPEC_EDITOR_RELATION_SCOPE__ = "internal";
  var id = 0,
    pend = {};

  if (typeof INITIAL_ELEMENT !== "undefined" && INITIAL_ELEMENT) {
    console.log(
      "[bridge] INITIAL_ELEMENT=" +
        INITIAL_ELEMENT +
        " — will click after load",
    );
    (function retry(n) {
      var chips = document.querySelectorAll(".chip");
      for (var i = 0; i < chips.length; i++) {
        var c = chips[i];
        var name = c.textContent.replace(/ /g, "_").replace(/[^a-z_]/g, "");
        if (
          name === INITIAL_ELEMENT ||
          c.getAttribute("data-aspect") === INITIAL_ELEMENT
        ) {
          console.log("[bridge] clicking chip for " + INITIAL_ELEMENT);
          c.click();
          return;
        }
      }
      if (n < 30)
        setTimeout(function () {
          retry(n + 1);
        }, 200);
    })(0);
  }

  // Pass diagram engine setting to frontend
  if (typeof DIAGRAM_ENGINE !== "undefined") {
    window.__SPEC_EDITOR_DIAGRAM_ENGINE__ = DIAGRAM_ENGINE;
  }

  window.addEventListener("message", function (e) {
    var m = e.data;
    if (!m) return;
    if (m.type === "specEditor") {
      if (m.event === "projectLoaded" || m.event === "mcpReady") {
        location.reload();
      } else if (m.event === "simulateClick" && m.nodeId) {
        // Test helper: simulate a click on an SVG node
        var svgNode = document.querySelector('g[id="' + m.nodeId + '"]');
        if (svgNode) {
          // Dispatch mousedown + mouseup to trigger React handlers
          var rect = svgNode.getBoundingClientRect();
          var cx = rect.left + rect.width / 2;
          var cy = rect.top + rect.height / 2;
          var opts = { clientX: cx, clientY: cy, bubbles: true };
          svgNode.dispatchEvent(new MouseEvent("mousedown", opts));
          svgNode.dispatchEvent(new MouseEvent("mouseup", opts));
          console.log("[bridge] simulateClick dispatched on " + m.nodeId);
        }
      } else if (m.event === "selectElement" && m.elementId) {
        // Only try to match aspect chips (not diagram type buttons)
        (function retryClick(n) {
          var chips = document.querySelectorAll(".aspect-chips .chip");
          for (var i = 0; i < chips.length; i++) {
            var c = chips[i];
            var name = c.textContent.replace(/ /g, "_").replace(/[^a-z_]/g, "");
            if (name === m.elementId) {
              console.log("[bridge] FOUND CHIP! clicking " + name);
              c.click();
              return;
            }
          }
          if (n < 20)
            setTimeout(function () {
              retryClick(n + 1);
            }, 200);
        })(0);
      }
    } else if (m.id && pend[m.id]) {
      pend[m.id](m);
      delete pend[m.id];
    }
  });

  window.__vscode_mcp = function (method, params) {
    return new Promise(function (resolve, reject) {
      var cid = ++id;
      pend[cid] = function (m) {
        if (m.error) reject(new Error(m.error));
        else resolve(m.result.result || m.result);
      };
      v.postMessage({
        type: "mcp",
        body: { jsonrpc: "2.0", id: cid, method: method, params: params || {} },
      });
    });
  };

  var _fetch = window.fetch;
  window.fetch = function (url, opts) {
    if (
      typeof url === "string" &&
      (url.includes("/mcp") || url.includes("/api/mcp"))
    ) {
      var body = opts && opts.body ? JSON.parse(opts.body) : {};
      return window
        .__vscode_mcp(body.method || "tools/call", body.params || {})
        .then(function (r) {
          return new Response(
            JSON.stringify({ jsonrpc: "2.0", id: body.id, result: r }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        })
        .catch(function (e) {
          return new Response(
            JSON.stringify({
              jsonrpc: "2.0",
              id: body.id,
              error: { message: e.message },
            }),
            { status: 502, headers: { "Content-Type": "application/json" } },
          );
        });
    }
    return _fetch.apply(this, arguments);
  };

  var origLog = console.log;
  console.log = function () {
    origLog.apply(console, arguments);
    v.postMessage({ type: "log", text: Array.from(arguments).join(" ") });
  };
  var origErr = console.error;
  console.error = function () {
    origErr.apply(console, arguments);
    v.postMessage({
      type: "log",
      text: "ERROR: " + Array.from(arguments).join(" "),
    });
  };

  window.loadMermaid = function (cb) {
    if (window.mermaid) {
      cb(window.mermaid);
      return;
    }
    var s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js";
    s.onload = function () {
      window.mermaid.initialize({
        startOnLoad: false,
        theme: "dark",
        maxEdges: 3000,
        maxTextSize: 90000,
        securityLevel: "loose",
        suppressErrorRendering: true,
        themeVariables: {
          primaryColor: "#4A90D9",
          primaryTextColor: "#eee",
          lineColor: "#888",
          secondaryColor: "#16213e",
          tertiaryColor: "#1a1a2e",
        },
      });
      cb(window.mermaid);
    };
    document.head.appendChild(s);
  };

  var scale = 1,
    panX = 0,
    panY = 0,
    isPanning = 0,
    lastX = 0,
    lastY = 0,
    panStartX = 0,
    panStartY = 0;
  document.addEventListener("wheel", function (e) {
    var area = e.target.closest(".diagram-area");
    if (!area) return;
    e.preventDefault();

    var ab = area.getBoundingClientRect();

    // Mouse position relative to container top-left
    var mx = e.clientX - ab.left;
    var my = e.clientY - ab.top;

    // Point in SVG-space before zoom: (mx - panX) / oldScale
    var svgX = (mx - panX) / scale;
    var svgY = (my - panY) / scale;

    // Apply zoom
    scale = Math.max(
      0.3,
      scale - e.deltaY * 0.01 * (WINDOW_ZOOM_SENSITIVITY || 1),
    );

    // After zoom, adjust pan so the same SVG-space point stays under cursor
    panX = mx - svgX * scale;
    panY = my - svgY * scale;

    applyTransform();
  });
  document.addEventListener("mousedown", function (e) {
    if (e.target.closest(".diagram-area")) {
      isPanning = 1;
      lastX = e.clientX;
      lastY = e.clientY;
      panStartX = e.clientX;
      panStartY = e.clientY;
    }
  });
  document.addEventListener("mousemove", function (e) {
    if (isPanning) {
      panX += e.clientX - lastX;
      panY += e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;
      applyTransform();
    }
  });
  var lastClickTime = 0;
  var lastClickNodeId = "";
  document.addEventListener("mouseup", function (e) {
    if (
      Math.abs(panStartX - e.clientX) < 5 &&
      Math.abs(panStartY - e.clientY) < 5
    ) {
      isPanning = 0;

      var el = e.target;
      var pid = "";
      while (el && !pid) {
        if (el.tagName === "foreignObject" && el.parentElement) {
          var g1 = el.parentElement;
          var g2 = g1.parentElement;
          var fullId = (g2 && g2.id) || "";
          var m = fullId.match(/[A-Z]+[_-]\d+/);
          pid = m ? m[0].replace(/_/g, "-") : "";
        } else {
          var gid = el.id || "";
          if (
            (el.tagName === "g" || el.tagName === "G") &&
            /^[A-Z]+[_-]\d+/.test(gid)
          ) {
            pid = gid.replace(/_/g, "-");
          }
        }
        el = el.parentElement;
      }
      if (!pid) {
        isPanning = 0;
        return;
      }

      var now = Date.now();
      console.log(
        "[bridge] click detect: pid=" +
          pid +
          " lastId=" +
          lastClickNodeId +
          " delta=" +
          (now - lastClickTime),
      );
      if (pid === lastClickNodeId && now - lastClickTime < 500) {
        console.log("[bridge] SVG node DOUBLE-click:", pid);
        v.postMessage({ type: "diagramNodeDblClick", nodeId: pid });
        lastClickTime = 0;
        lastClickNodeId = "";
      } else {
        // First click: remember it, delay the single-click action
        lastClickTime = now;
        lastClickNodeId = pid;
        setTimeout(function () {
          // If still the same node after 300ms, it was a single click
          if (lastClickNodeId === pid) {
            console.log("[bridge] SVG node click (delayed):", pid);
            v.postMessage({ type: "diagramNodeClick", nodeId: pid });
            lastClickTime = 0;
            lastClickNodeId = "";
          }
        }, 300);
      }
      return;
    }
    isPanning = 0;
  });
  function applyTransform() {
    var s = document.querySelector(".diagram-area svg");
    if (s) {
      s.style.transform =
        "translate(" + panX + "px," + panY + "px) scale(" + scale + ")";
      s.style.transformOrigin = "0 0";
      s.style.cursor = isPanning ? "grabbing" : "grab";
    }
  }

  function fitWidth() {
    var area = document.querySelector(".diagram-area");
    var svg = area && area.querySelector("svg");
    if (!svg) return;
    svg.style.transform = "";
    var ab = area.getBoundingClientRect();
    var sb = svg.getBoundingClientRect();
    if (!sb.width || !sb.height) return;
    scale = Math.min(ab.width / sb.width, ab.height / sb.height, 3);
    panX = 0;
    panY = 0;
    applyTransform();
  }
  (function waitArea() {
    var a = document.querySelector(".diagram-area");
    if (a) {
      a.style.position = "relative";
      var btns = document.createElement("div");
      btns.style.cssText =
        "position:absolute;top:4px;right:4px;display:flex;gap:3px;z-index:10";

      function refreshDiagram() {
        // Post a message to the same window so the React app picks it up
        // (v.postMessage goes to VSCode host, not to the window listener).
        window.postMessage({ type: "specEditor", event: "refreshDiagram" }, "*");
      }

      // Relation scope toggle: internal / external / all
      var scopeModes = ["internal", "external", ""];
      var scopeLabels = ["Ri", "Re", "R"];
      var scopeIdx = 0;
      var btnR = document.createElement("button");
      btnR.textContent = scopeLabels[scopeIdx];
      btnR.title = "Relations: internal only";
      btnR.style.cssText =
        "width:22px;height:22px;font-size:11px;font-weight:700;line-height:1;border:1px solid #555;border-radius:3px;background:#2a3a5a;color:#8af;cursor:pointer;padding:0;text-align:center";
      btnR.onclick = function () {
        scopeIdx = (scopeIdx + 1) % 3;
        btnR.textContent = scopeLabels[scopeIdx];
        window.__SPEC_EDITOR_RELATION_SCOPE__ = scopeModes[scopeIdx];
        if (scopeModes[scopeIdx] === "") {
          btnR.style.background = "#222";
          btnR.style.color = "#aaa";
          btnR.title = "Relations: all (click to change)";
        } else {
          btnR.style.background = "#2a3a5a";
          btnR.style.color = "#8af";
          btnR.title =
            "Relations: " + scopeModes[scopeIdx] + " (click to change)";
        }
        refreshDiagram();
      };
      // Set initial scope to internal
      window.__SPEC_EDITOR_RELATION_SCOPE__ = scopeModes[scopeIdx];
      btns.appendChild(btnR);

      // Auto-refresh toggle
      var btnA = document.createElement("button");
      btnA.textContent = "A";
      btnA.title = "Auto-refresh OFF";
      btnA.style.cssText =
        "width:22px;height:22px;font-size:11px;font-weight:700;line-height:1;border:1px solid #555;border-radius:3px;background:#222;color:#aaa;cursor:pointer;padding:0;text-align:center";
      btnA._auto = false;
      btnA._dirty = false;
      btnA.onclick = function () {
        btnA._auto = !btnA._auto;
        if (btnA._auto) {
          btnA.style.background = "#2a5a2a";
          btnA.style.color = "#5f5";
          btnA.title = "Auto-refresh ON";
          if (btnA._dirty) {
            refreshDiagram();
            btnA._dirty = false;
          }
        } else {
          btnA.style.background = "#222";
          btnA.style.color = "#aaa";
          btnA.title = "Auto-refresh OFF";
        }
      };

      // Listen for elementsChanged from VSCode host
      window.addEventListener("message", function (e) {
        var m = e.data;
        if (m && m.type === "specEditor" && m.event === "elementsChanged") {
          if (btnA._auto) {
            refreshDiagram();
          } else {
            btnA._dirty = true;
            btnA.style.background = "#5a3a00";
            btnA.style.color = "#fa0";
            btnA.title = "Diagram outdated — click to refresh";
          }
        }
      });

      btns.appendChild(btnA);

      var btnW = document.createElement("button");
      btnW.textContent = "W";
      btnW.title = "Fit Width";
      btnW.style.cssText =
        "width:22px;height:22px;font-size:11px;font-weight:700;line-height:1;border:1px solid #555;border-radius:3px;background:#222;color:#aaa;cursor:pointer;padding:0;text-align:center";
      btnW.onclick = fitWidth;
      btns.appendChild(btnW);
      // Download SVG button
      var btnD = document.createElement("button");
      btnD.innerHTML = "&#8595;";
      btnD.title = "Download SVG";
      btnD.style.cssText = btnW.style.cssText;
      btnD.onclick = function () {
        var svg = document.querySelector(".diagram-area svg");
        if (!svg) return;
        var clone = svg.cloneNode(true);
        var data = new XMLSerializer().serializeToString(clone);
        v.postMessage({ type: "downloadSvg", svg: data });
      };
      btns.appendChild(btnD);
      a.appendChild(btns);
    } else setTimeout(waitArea, 200);
  })();

  // Re-fit on resize (panel resize, metrics toggle, etc.)
  (function observeResize() {
    var ro = new ResizeObserver(function () {
      // Debounce — wait for resize to settle
      clearTimeout(ro._timer);
      ro._timer = setTimeout(function () {
        fitWidth();
      }, 150);
    });
    (function wait() {
      var a = document.querySelector(".diagram-area");
      if (a) {
        ro.observe(a);
      } else setTimeout(wait, 500);
    })();
  })();

  (function autoFit() {
    var s = document.querySelector(".diagram-area svg");
    if (s) {
      setTimeout(function () {
        s.style.transform = "";
        var a = document.querySelector(".diagram-area");
        var ab = a.getBoundingClientRect();
        var sb = s.getBoundingClientRect();
        if (sb.width && sb.height) {
          scale = Math.min(ab.width / sb.width, ab.height / sb.height, 2);
          panX = 0;
          panY = 0;
          applyTransform();
        }
      }, 300);
    } else setTimeout(autoFit, 200);
  })();

  var errObs = new MutationObserver(function () {
    var b = document.querySelector(".error-banner");
    if (b && b.textContent && !b.dataset.reported) {
      b.dataset.reported = "1";
      v.postMessage({
        type: "log",
        text: "ERROR BANNER: " + b.textContent.trim().replace(/\n/g, " | "),
      });
    }
  });
  setTimeout(function () {
    errObs.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  }, 500);
})();

// Global JS error reporter — sends all uncaught errors to VSCode log
window.addEventListener("error", function (e) {
  var msg = "[JS ERROR] " + e.message;
  if (e.filename) msg += " at " + e.filename + ":" + e.lineno;
  if (e.error && e.error.stack) msg += "\n" + e.error.stack;
  (window.__vscodeApi || v).postMessage({ type: "log", text: msg });
});
window.addEventListener("unhandledrejection", function (e) {
  var msg =
    "[JS UNHANDLED PROMISE] " +
    (e.reason && e.reason.message ? e.reason.message : String(e.reason));
  if (e.reason && e.reason.stack) msg += "\n" + e.reason.stack;
  (window.__vscodeApi || v).postMessage({ type: "log", text: msg });
});

// Tracing: report React render state
(function traceReact() {
  var attempts = 0;
  function check() {
    attempts++;
    var area = document.querySelector(".diagram-area");
    var svg = area && area.querySelector("svg");
    var chips = document.querySelectorAll(".aspect-chips .chip");
    var errorBanner = document.querySelector(".error-banner");
    var msg = "[TRACE] attempt " + attempts;
    msg += " chips=" + chips.length;
    msg += " area=" + !!area;
    msg += " svg=" + !!svg;
    msg +=
      " error=" +
      (errorBanner ? errorBanner.textContent.trim().substring(0, 80) : "none");
    (window.__vscodeApi || v).postMessage({ type: "log", text: msg });
    if (!svg && attempts < 15) setTimeout(check, 1000);
  }
  setTimeout(check, 2000);
})();

// Notify VSCode when diagram area gets SVG content
(function notifyReady() {
  var done = false;
  function check() {
    if (done) return;
    var svg = document.querySelector(".diagram-area svg");
    if (svg) {
      done = true;
      (window.__vscodeApi || v).postMessage({
        type: "log",
        text: "[READY] diagram SVG detected in DOM",
      });
    } else {
      setTimeout(check, 500);
    }
  }
  setTimeout(check, 1500);
})();

// Signal to VS Code that diagram is ready (SVG in DOM)
(function signalDiagramReady() {
  var done = false;
  function check() {
    if (done) return;
    var svg = document.querySelector(".diagram-area svg");
    if (svg) {
      done = true;
      (window.__vscodeApi || v).postMessage({
        type: "diagramReady",
        svgCount: document.querySelectorAll(".diagram-area svg").length,
      });
      console.log(
        "[bridge] diagramReady sent, SVG count=" +
          document.querySelectorAll(".diagram-area svg").length,
      );
    } else {
      setTimeout(check, 500);
    }
  }
  setTimeout(check, 2000);
})();

// Monitor diagram rendering — check if Mermaid.render actually produces SVG
(function monitorRender() {
  var origRender = window.mermaid && window.mermaid.render;
  if (!origRender) {
    // Mermaid not loaded yet — wait and retry
    var checkCount = 0;
    var checkInterval = setInterval(function () {
      checkCount++;
      if (window.mermaid && window.mermaid.render) {
        clearInterval(checkInterval);
        wrapRender(window.mermaid);
      } else if (checkCount > 60) {
        clearInterval(checkInterval);
        console.log("[bridge] Mermaid never loaded after 60 attempts");
      }
    }, 1000);
    return;
  }
  wrapRender(window.mermaid);

  function wrapRender(m) {
    var orig = m.render.bind(m);
    m.render = function (id, code) {
      console.log(
        "[bridge] mermaid.render CALLED id=" +
          id +
          " code_preview=" +
          (code || "").substring(0, 50),
      );
      var promise = orig(id, code);
      promise
        .then(function (result) {
          var svgLen = result && result.svg ? result.svg.length : 0;
          console.log(
            "[bridge] mermaid.render RESOLVED id=" + id + " svg_len=" + svgLen,
          );
        })
        .catch(function (err) {
          console.log(
            "[bridge] mermaid.render REJECTED id=" +
              id +
              " error=" +
              ((err && err.message) || err),
          );
        });
      return promise;
    };
  }
})();

// Aggressive SVG checker — runs every 500ms forever until SVG found
(function aggressiveSvgCheck() {
  var attempts = 0;
  var done = false;
  function check() {
    if (done) return;
    attempts++;
    var area = document.querySelector(".diagram-area");
    var svg = area && area.querySelector("svg");
    if (svg) {
      done = true;
      (window.__vscodeApi || v).postMessage({
        type: "diagramReady",
        svgCount: 1,
        attempts: attempts,
      });
      console.log(
        "[bridge] AGGRESSIVE: SVG found after " + attempts + " attempts",
      );
    } else {
      if (attempts <= 5 || attempts % 10 === 0) {
        console.log(
          "[bridge] AGGRESSIVE check #" +
            attempts +
            ": area=" +
            !!area +
            " svg=" +
            !!svg,
        );
      }
      setTimeout(check, 500);
    }
  }
  setTimeout(check, 500);
})();

// Diagram type validator: checks backend + mermaid.render
// Validate diagram types for an aspect.
// Calls generate_diagram (backend) then mermaid.render (frontend).
// Sets window._validDiagramTypes["aspect:type"] = true/false.
// React polls this to hide broken diagram type buttons.
window._validateDiagramTypes = function (aspect) {
  console.log("[bridge] _validateDiagramTypes START aspect=" + aspect);
  var types = [
    "graph",
    "flowchart",
    "sequence",
    "class",
    "er",
    "state",
    "gantt",
    "pie",
    "mindmap",
    "timeline",
    "sankey",
  ];
  var sandbox = document.getElementById("diagram-validate-sandbox");
  if (!sandbox) {
    sandbox = document.createElement("div");
    sandbox.id = "diagram-validate-sandbox";
    sandbox.style.cssText =
      "position:fixed;left:-9999px;top:-9999px;width:100px;height:100px;overflow:hidden;display:none;";
    document.body.appendChild(sandbox);
  }

  types.forEach(function (dt) {
    window
      .__vscode_mcp("tools/call", {
        name: "generate_diagram",
        arguments: { aspect: aspect, diagram_type: dt },
      })
      .then(function (diag) {
        var inner2 = typeof diag === "string" ? JSON.parse(diag) : diag;
        if (inner2.content && inner2.content[0] && inner2.content[0].text) {
          try {
            inner2 = JSON.parse(inner2.content[0].text);
          } catch (e) {}
        }
        if (inner2.error) {
          return;
        } // backend error — button hidden by React validation
        var code = inner2.diagram || inner2.mermaid || "";
        if (!code) return;

        window.loadMermaid(function (m) {
          m.render("_vld-" + aspect + "-" + dt, code).then(
            function () {
              console.log(
                "[bridge] _validateDiagramTypes VALID " + aspect + ":" + dt,
              );
              if (!window._validDiagramTypes) window._validDiagramTypes = {};
              window._validDiagramTypes[aspect + ":" + dt] = true;
              // Clean up sandbox render
              sandbox.innerHTML = "";
            },
            function (err) {
              console.log(
                "[bridge] _validateDiagramTypes INVALID " +
                  aspect +
                  ":" +
                  dt +
                  " error=" +
                  ((err && err.message) || err),
              );
              if (!window._validDiagramTypes) window._validDiagramTypes = {};
              window._validDiagramTypes[aspect + ":" + dt] = false;
              sandbox.innerHTML = "";
            },
          );
        });
      })
      .catch(function () {});
  });
};

// Highlight SVG nodes and edges on hover
(function setupHoverHighlight() {
  var activeNode = null;
  var activeEdges = [];

  function clearHighlight() {
    if (activeNode) {
      activeNode
        .querySelectorAll("rect, .label-container > rect, foreignObject > div")
        .forEach(function (r) {
          r.style.filter = "";
        });
      activeNode = null;
    }
    activeEdges.forEach(function (e) {
      e.style.strokeWidth = "";
      e.style.stroke = "";
    });
    activeEdges = [];
  }

  function highlightNode(g) {
    clearHighlight();
    activeNode = g;
    // Highlight the node rect
    g.querySelectorAll(
      "rect, .label-container > rect, foreignObject > div",
    ).forEach(function (r) {
      r.style.filter =
        "brightness(1.4) drop-shadow(0 0 6px rgba(74,144,217,0.6))";
    });
    // Highlight connected edges
    var nodeId = g.id || "";
    document
      .querySelectorAll(
        ".diagram-area svg .edgePath, .diagram-area svg .edgePaths path",
      )
      .forEach(function (edge) {
        var label =
          edge.getAttribute("aria-label") || edge.getAttribute("class") || "";
        if (label.indexOf(nodeId) !== -1) {
          edge.style.strokeWidth = "2.5";
          edge.style.stroke = "#4A90D9";
          activeEdges.push(edge);
        }
      });
  }

  document.addEventListener("mouseover", function (e) {
    var g = e.target.closest("g.node");
    if (g && g.id) {
      highlightNode(g);
      return;
    }
    clearHighlight();
  });
})();

// Edge click — navigate to target element
(function setupEdgeClick() {
  // Find edge target from SVG path labels (Mermaid puts edge labels in path aria-label or text)
})();

// Edge click — find target node by path coordinates
(function setupEdgeClick() {
  document.addEventListener(
    "mouseup",
    function (e) {
      console.log("[bridge] MOUSEUP FIRED");
      // Only handle clicks (not pans)
      if (typeof panX === "undefined") return;
      if (Math.abs(typeof lastX !== "undefined" ? e.clientX - lastX : 0) > 3)
        return;

      var edgeGroup = e.target.closest("g.edgePaths, g.edgePath, g.edgeLabel");
      if (!edgeGroup) return;

      // Get the first path element in the edge group
      var path = edgeGroup.querySelector("path");
      if (!path) return;

      // Get the end point of the path (target node)
      var d = path.getAttribute("d") || "";
      // Mermaid paths look like: "M x1,y1 L x2,y2" or cubic bezier
      // Extract the last coordinate pair as the target end
      var coords = d.match(/[\d.]+/g);
      if (!coords || coords.length < 2) return;
      var endX = parseFloat(coords[coords.length - 2]);
      var endY = parseFloat(coords[coords.length - 1]);

      // Find the closest node to the end point
      var svg = edgeGroup.closest("svg");
      if (!svg) return;
      var nodes = svg.querySelectorAll("g.node");
      if (!nodes.length) return;

      var bestNode = null;
      var bestDist = Infinity;
      nodes.forEach(function (n) {
        // Get node center from its transform attribute
        var tr = n.getAttribute("transform") || "";
        var m = tr.match(/translate\(([\d.]+),\s*([\d.]+)\)/);
        if (m) {
          var nx = parseFloat(m[1]) + 60; // rough center offset
          var ny = parseFloat(m[2]) + 25;
          var dist = Math.hypot(endX - nx, endY - ny);
          if (dist < bestDist) {
            bestDist = dist;
            bestNode = n;
          }
        }
      });

      if (bestNode && bestNode.id) {
        var originalId = bestNode.id.replace(/_/g, "-");
        console.log(
          "[bridge] edge click -> target=" +
            originalId +
            " dist=" +
            bestDist.toFixed(0),
        );
        v.postMessage({ type: "diagramEdgeClick", targetId: originalId });
      }
    },
    true,
  );
})();

// Clean up Mermaid error text nodes left in diagram-area
(function cleanupErrors() {
  setInterval(function () {
    var area = document.querySelector(".diagram-area");
    if (!area) return;
    var walker = document.createTreeWalker(area, NodeFilter.SHOW_TEXT);
    var node;
    while ((node = walker.nextNode())) {
      if (
        node.textContent &&
        (node.textContent.indexOf("Syntax error") !== -1 ||
          node.textContent.indexOf("mermaid version") !== -1)
      ) {
        node.textContent = "";
        // Also remove parent if it's a text/SVG element holding the error
        var p = node.parentElement;
        if (p && (p.tagName === "text" || p.tagName === "tspan"))
          p.textContent = "";
      }
    }
  }, 2000);
})();
