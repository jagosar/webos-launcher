import type { ServiceBridge, Tile } from "./types";

declare global {
  interface Window {
    PalmServiceBridge?: new () => ServiceBridge;
  }
}

export class LaunchError extends Error {
  name = "LaunchError";
}

export const LAUNCH_URI = "luna://com.webos.applicationManager/launch";
export const LG_HOME_ID = "com.webos.app.home";
export const BROWSER_ID = "com.webos.app.browser";

const UNKNOWN_ERROR_TEXT = "Error desconocido";

export function defaultBridgeFactory(): ServiceBridge {
  if (typeof window === "undefined" || !window.PalmServiceBridge) {
    throw new LaunchError("PalmServiceBridge no disponible");
  }
  return new window.PalmServiceBridge();
}

export function launchApp(
  appId: string,
  bridgeFactory?: () => ServiceBridge,
  params?: { [key: string]: string }
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    let bridge: ServiceBridge;
    try {
      bridge = (bridgeFactory || defaultBridgeFactory)();
    } catch (error) {
      reject(error);
      return;
    }

    let isSettled = false;
    bridge.onservicecallback = (message: string): void => {
      if (isSettled) {
        return;
      }
      isSettled = true;
      let response: { returnValue?: unknown; errorText?: unknown } | null;
      try {
        response = JSON.parse(message);
      } catch (error) {
        reject(new LaunchError("Respuesta inválida"));
        return;
      }
      if (response !== null && typeof response === "object" && response.returnValue === true) {
        resolve();
        return;
      }
      const errorText =
        response !== null && typeof response === "object" && typeof response.errorText === "string"
          ? response.errorText
          : UNKNOWN_ERROR_TEXT;
      reject(new LaunchError(errorText));
    };
    bridge.call(LAUNCH_URI, JSON.stringify(params ? { id: appId, params } : { id: appId }));
  });
}

export function launchTile(tile: Tile, bridgeFactory?: () => ServiceBridge): Promise<void> {
  if (tile.type !== "web") {
    return launchApp(tile.id, bridgeFactory);
  }
  if (!tile.url) {
    return Promise.reject(new LaunchError("Falta la url"));
  }
  return launchApp(BROWSER_ID, bridgeFactory, { target: tile.url });
}
