import { buildApp } from "./app.js";
import { config } from "./lib/config.js";

async function main() {
  const app = await buildApp();

  try {
    await app.listen({
      port: config.PORT,
      host: "0.0.0.0"
    });
  } catch (error) {
    app.log.error(error);
    process.exit(1);
  }
}

void main();
