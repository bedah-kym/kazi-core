"""
Base connector class for MCP integrations
"""
from typing import Dict, Any


class BaseConnector:
    """Base class for all external service connectors"""
    
    async def execute(self, intent: Dict[str, Any], user: Any) -> Dict[str, Any]:
        """
        Execute the connector action based on intent
        
        Args:
            intent: Dictionary containing action and parameters
            user: User object making the request
            
        Returns:
            Dictionary with execution results
        """
        raise NotImplementedError("Subclasses must implement execute() method")
