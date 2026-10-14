// craco.config.js
const path = require("path");

// O arquivo do ambiente vem ANTES do `.env` geral, e a ordem é o ponto.
//
// Este `dotenv.config()` roda antes de o CRA carregar os arquivos dele, e o
// dotenv nunca sobrescreve variável já definida. Carregando só o `.env`, o
// `REACT_APP_BACKEND_URL=http://localhost:8000` do desenvolvimento entrava
// primeiro e vencia o `.env.production`, invertendo a precedência que o CRA
// documenta. O build de produção saía com o endereço do localhost cravado
// dentro do bundle, e o navegador de quem abria o site tentava falar com o
// localhost dele: erro de CORS em "loopback address space", que não parece o
// que é. Aconteceu no primeiro deploy.
require("dotenv").config({
  path: path.resolve(__dirname, `.env.${process.env.NODE_ENV || "development"}`),
});
require("dotenv").config();

function makeDevServerV5Compatible(devServerConfig) {
  const {
    https,
    onAfterSetupMiddleware,
    onBeforeSetupMiddleware,
    onListening,
    setupMiddlewares,
    ...compatibleConfig
  } = devServerConfig;

  compatibleConfig.server =
    typeof https === "object"
      ? { type: "https", options: https }
      : https
        ? "https"
        : "http";
  compatibleConfig.headers = {
    ...compatibleConfig.headers,
    "Cross-Origin-Resource-Policy": "same-origin",
  };

  if (onBeforeSetupMiddleware || setupMiddlewares) {
    compatibleConfig.setupMiddlewares = (middlewares, devServer) => {
      if (onBeforeSetupMiddleware) {
        onBeforeSetupMiddleware(devServer);
      }

      return setupMiddlewares
        ? setupMiddlewares(middlewares, devServer)
        : middlewares;
    };
  }

  compatibleConfig.onListening = (devServer) => {
    devServer.close ??= (callback) => devServer.stopCallback(callback);

    if (onListening) {
      onListening(devServer);
    }
    if (onAfterSetupMiddleware) {
      onAfterSetupMiddleware(devServer);
    }
  };

  return compatibleConfig;
}

let webpackConfig = {
  eslint: {
    configure: {
      extends: ["plugin:react-hooks/recommended"],
      rules: {
        "react-hooks/rules-of-hooks": "error",
        "react-hooks/exhaustive-deps": "warn",
      },
    },
  },
  webpack: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
    configure: (webpackConfig) => {

      // Add ignored patterns to reduce watched directories
        webpackConfig.watchOptions = {
          ...webpackConfig.watchOptions,
          ignored: [
            '**/node_modules/**',
            '**/.git/**',
            '**/build/**',
            '**/dist/**',
            '**/coverage/**',
            '**/public/**',
        ],
      };

      return webpackConfig;
    },
  },
};

webpackConfig.devServer = (devServerConfig) => devServerConfig;

const configureDevServer = webpackConfig.devServer;
webpackConfig.devServer = (devServerConfig) =>
  makeDevServerV5Compatible(configureDevServer(devServerConfig));

module.exports = webpackConfig;
