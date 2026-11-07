// SPDX-License-Identifier: Apache-2.0
// Runs Python off the page's thread, so a long analysis can be stopped by ending the worker.
importScripts("https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.js");

let py = null;

async function boot() {
  py = await loadPyodide();
  const files = await (await fetch("manifest.json")).json();
  await Promise.all(files.map(async (name) => {
    const text = await (await fetch("repo/" + name)).text();
    const path = "/repo/" + name;
    py.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
    py.FS.writeFile(path, text);
  }));
  py.runPython("import sys; sys.path.insert(0, '/repo/site'); import playground");
  postMessage({ ready: true });
}

const booted = boot().catch((e) => postMessage({ failed: String(e) }));

onmessage = async ({ data: { id, name, args } }) => {
  await booted;
  try {
    const call = py.globals.get("playground").call;
    postMessage({ id, result: JSON.parse(call(name, JSON.stringify(args))) });
  } catch (e) {
    postMessage({ id, result: { verdict: "error", message: String(e) } });
  }
};
