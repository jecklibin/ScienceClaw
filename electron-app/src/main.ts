import { app, BrowserWindow, ipcMain, dialog, Tray, Menu, shell } from 'electron';
import * as path from 'path';
import { ConfigManager } from './config';
import { ProcessManager } from './process-manager';

let mainWindow: BrowserWindow | null = null;
let wizardWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let configManager: ConfigManager;
let processManager: ProcessManager | null = null;
let isQuitting = false;

// In development, use frontend dev server; in production, use backend
const FRONTEND_URL = app.isPackaged
  ? 'http://127.0.0.1:12001'
  : 'http://localhost:5173';

/**
 * Create the wizard window
 */
function createWizardWindow() {
  wizardWindow = new BrowserWindow({
    width: 600,
    height: 500,
    resizable: false,
    frame: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  wizardWindow.loadFile(path.join(__dirname, 'wizard', 'wizard.html'));

  wizardWindow.on('closed', () => {
    wizardWindow = null;
  });
}

/**
 * Create the main application window
 */
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Load frontend URL
  mainWindow.loadURL(FRONTEND_URL);

  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow?.hide();
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

/**
 * Create system tray icon
 */
function createTray() {
  // Use a default icon (you'll need to provide icon.ico)
  const iconPath = path.join(__dirname, '..', 'resources', 'icon.ico');
  tray = new Tray(iconPath);

  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Show RpaClaw',
      click: () => {
        mainWindow?.show();
      },
    },
    {
      label: 'Quit',
      click: () => {
        isQuitting = true;
        app.quit();
      },
    },
  ]);

  tray.setToolTip('RpaClaw');
  tray.setContextMenu(contextMenu);

  tray.on('click', () => {
    mainWindow?.show();
  });
}

/**
 * Initialize application
 */
async function initialize() {
  configManager = new ConfigManager();

  if (configManager.isFirstRun()) {
    // Show wizard
    createWizardWindow();
  } else {
    // Load config and start services
    const config = configManager.load();
    if (!config) {
      console.error('Failed to load config');
      app.quit();
      return;
    }

    // Start backend services
    processManager = new ProcessManager(config.homeDir);
    try {
      await processManager.startBackend();
      await processManager.startTaskService();
    } catch (error) {
      console.error('Failed to start services:', error);
      dialog.showErrorBox('Startup Error', `Failed to start services: ${error}`);
      app.quit();
      return;
    }

    // Create main window and tray
    createMainWindow();
    createTray();
  }
}

// App lifecycle
app.on('ready', initialize);

app.on('window-all-closed', () => {
  // On macOS, keep app running when all windows closed
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (mainWindow === null) {
    createMainWindow();
  } else {
    mainWindow.show();
  }
});

app.on('before-quit', async () => {
  isQuitting = true;
  if (processManager) {
    await processManager.stopAll();
  }
});

// IPC Handlers

// Config
ipcMain.handle('get-home-dir', () => {
  const config = configManager.get();
  return config?.homeDir || '';
});

ipcMain.handle('set-home-dir', async (event, newPath: string) => {
  const config = configManager.get();
  if (config) {
    config.homeDir = newPath;
    configManager.save(config);

    // Restart required
    dialog.showMessageBox({
      type: 'info',
      title: 'Restart Required',
      message: 'Please restart RpaClaw for changes to take effect.',
      buttons: ['OK'],
    });
  }
});

// Process status
ipcMain.handle('get-backend-status', () => {
  return processManager?.getBackendStatus() || { running: false, port: 12001 };
});

ipcMain.handle('get-task-service-status', () => {
  return processManager?.getTaskServiceStatus() || { running: false, port: 12002 };
});

// App control
ipcMain.on('restart-app', () => {
  app.relaunch();
  app.quit();
});

ipcMain.on('open-external', (event, url: string) => {
  shell.openExternal(url);
});

// Wizard
ipcMain.handle('get-default-home-dir', () => {
  return configManager.getDefaultHomeDir();
});

ipcMain.handle('select-directory', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openDirectory', 'createDirectory'],
    title: 'Select Home Directory',
  });

  if (result.canceled) {
    return null;
  }

  return result.filePaths[0];
});

ipcMain.handle('validate-home-dir', (event, dirPath: string) => {
  return configManager.validateHomeDir(dirPath);
});

ipcMain.handle('initialize-home-dir', async (event, dirPath: string) => {
  try {
    configManager.initializeHomeDir(dirPath);

    // Save config
    const config = {
      homeDir: dirPath,
      version: app.getVersion(),
    };
    configManager.save(config);

    return { success: true };
  } catch (error) {
    throw new Error(`Initialization failed: ${error}`);
  }
});

ipcMain.on('wizard-complete', () => {
  // Close wizard and relaunch app so it goes through normal initialize() path
  wizardWindow?.close();
  app.relaunch();
  app.quit();
});
