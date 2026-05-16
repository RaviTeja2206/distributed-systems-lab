import requests
import random
import logging

logger = logging.getLogger(__name__)


class ConsulClient:
    """
    Handles service discovery via Consul HTTP API.
    
    This is what replaces hardcoded IP addresses in production systems.
    Instead of connecting to 10.10.0.11:5672 directly, services ask
    Consul "give me a healthy RabbitMQ node" and connect to whatever
    Consul returns.
    """

    def __init__(self, consul_host="localhost", consul_port=8500):
        self.base_url = f"http://{consul_host}:{consul_port}"

    def get_healthy_service(self, service_name):
        """
        Returns a list of (address, port) tuples for healthy instances
        of the named service.
        
        Uses the health endpoint with passing=true to filter out
        any instances that are failing their health checks.
        """
        url = f"{self.base_url}/v1/health/service/{service_name}?passing=true"
        
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            services = response.json()
            
            if not services:
                logger.warning(f"No healthy instances of {service_name} found")
                return []

            return [
                (s["Service"]["Address"], s["Service"]["Port"])
                for s in services
            ]

        except requests.exceptions.ConnectionError:
            logger.error(f"Cannot reach Consul at {self.base_url}")
            return []
        except requests.exceptions.Timeout:
            logger.error("Consul request timed out")
            return []

    def get_random_healthy_service(self, service_name):
        """
        Returns a single random (address, port) tuple for the service.
        
        Random selection provides basic load balancing across healthy nodes.
        Production systems use weighted selection or sticky sessions,
        but random is correct for most cases.
        """
        instances = self.get_healthy_service(service_name)
        if not instances:
            return None
        return random.choice(instances)

    def get_service_catalog(self, service_name):
        """
        Returns ALL instances regardless of health status.
        Useful for debugging — see all registered nodes including unhealthy ones.
        """
        url = f"{self.base_url}/v1/catalog/service/{service_name}"
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Consul catalog query failed: {e}")
            return []