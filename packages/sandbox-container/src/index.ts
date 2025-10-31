import { createLogger } from '@repo/shared';
import { serve } from 'bun';
import { Container } from './core/container';
import { Router } from './core/router';
import { setupRoutes } from './routes/setup';

// Create module-level logger for server lifecycle events
const logger = createLogger({ component: 'container' });

async function createApplication(): Promise<{
  fetch: (req: Request) => Promise<Response>;
}> {
  // Initialize dependency injection container
  const container = new Container();
  await container.initialize();

  // Create and configure router
  const router = new Router(logger);

  // Add global CORS middleware
  router.use(container.get('corsMiddleware'));

  // Setup all application routes
  setupRoutes(router, container);

  return {
    fetch: (req: Request) => {
      // Subdomain routing: Check if request uses port-prefix subdomain pattern
      // Format: <port>-<identifier>.<domain> -> routes to /proxy/<port>
      // Example: 8080-myapp.localhost/api -> /proxy/8080/api
      const url = new URL(req.url);
      const subdomainMatch = url.hostname.match(/^(\d{4,5})-(.+)$/);
      
      if (subdomainMatch) {
        const portStr = subdomainMatch[1];
        const port = parseInt(portStr, 10);
        
        // Valid port range check (exclude control plane port 3000)
        if (port >= 1024 && port <= 65535 && port !== 3000) {
          logger.debug('Subdomain routing detected', {
            originalHostname: url.hostname,
            extractedPort: port,
            path: url.pathname
          });
          
          // Rewrite URL to use /proxy/{port} path, preserving protocol and host
          const proxyPath = `/proxy/${port}${url.pathname}${url.search}`;
          const proxyUrl = `${url.protocol}//${url.host}${proxyPath}`;
          
          // Create new request with proxy path
          const proxyRequest = new Request(proxyUrl, {
            method: req.method,
            headers: req.headers,
            body: req.body
          });
          
          logger.debug('Routing via proxy', {
            originalUrl: req.url,
            proxyPath,
            port
          });
          
          return router.route(proxyRequest);
        }
      }
      
      // Normal routing for non-subdomain requests
      return router.route(req);
    }
  };
}

// Initialize the application
const app = await createApplication();

// Start the Bun server
const server = serve({
  idleTimeout: 255,
  fetch: app.fetch,
  hostname: '0.0.0.0',
  port: 3000,
  // Enhanced WebSocket placeholder for future streaming features
  websocket: {
    async message() {
      // WebSocket functionality can be added here in the future
    }
  }
});

logger.info('Container server started', {
  port: server.port,
  hostname: '0.0.0.0'
});

// Graceful shutdown handling
process.on('SIGTERM', async () => {
  logger.info('Received SIGTERM, shutting down gracefully');

  // Get services for cleanup
  const container = new Container();
  if (container.isInitialized()) {
    try {
      // Cleanup services with proper typing
      const processService = container.get('processService');
      const portService = container.get('portService');

      // Cleanup processes (asynchronous - kills all running processes)
      await processService.destroy();

      // Cleanup ports (synchronous)
      portService.destroy();

      logger.info('Services cleaned up successfully');
    } catch (error) {
      logger.error(
        'Error during cleanup',
        error instanceof Error ? error : new Error(String(error))
      );
    }
  }

  process.exit(0);
});

process.on('SIGINT', async () => {
  logger.info('Received SIGINT, shutting down gracefully');
  process.emit('SIGTERM');
});
