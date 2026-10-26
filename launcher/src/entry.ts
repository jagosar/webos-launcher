import config from "./config.json";
import { startLauncher } from "./main";

const root = document.getElementById("app");
if (root === null) {
  throw new Error("#app no encontrado");
}

startLauncher({ root, config });
