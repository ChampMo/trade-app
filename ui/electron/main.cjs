// Electron is a window around the UI and nothing else. It holds no trading logic, no broker
// credentials and no state: closing this window must never stop the bot, which is why the core
// runs as its own process (D7).
const { app, BrowserWindow, shell } = require("electron");
const path = require("node:path");

const DEV_URL = process.env.VITE_DEV_SERVER_URL || "http://localhost:5173";
const isDev = !app.isPackaged;

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1100,
    minHeight: 700,
    title: "trade-app",
    backgroundColor: "#EEF1F5",
    webPreferences: {
      // The renderer only ever talks to the local core over HTTP; it needs no Node access.
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });

  if (isDev) {
    win.loadURL(DEV_URL);
  } else {
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }

  // External links open in the real browser, never inside the trading window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  // Quitting the UI does not touch the core. That separation is the point.
  if (process.platform !== "darwin") app.quit();
});
