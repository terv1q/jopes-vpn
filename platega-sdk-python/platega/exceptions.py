"""
Platega SDK Exceptions
"""


class PlategaException(Exception):
    """Base exception for Platega SDK"""
    pass


class PlategaAPIError(PlategaException):
    """Exception raised when Platega API returns an error"""
    
    def __init__(self, message: str, http_code: int = None, response_data: dict = None):
        super().__init__(message)
        self.http_code = http_code
        self.response_data = response_data or {}
    
    def __str__(self):
        if self.http_code:
            return f"[HTTP {self.http_code}] {self.args[0]}"
        return self.args[0]


class PlategaValidationError(PlategaException):
    """Exception raised when callback validation fails"""
    pass
