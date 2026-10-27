"""Reuse host login or existing automation credential; enforce cookie CSRF boundary."""
import hmac

def authorize(request, session_valid, automation_token='', public_origin='https://qiuyiwu.com'):
    header=request.headers.get('Authorization','')
    if header:
        return bool(header.startswith('Bearer ') and automation_token and hmac.compare_digest(header[7:].encode(),automation_token.encode()))
    if not session_valid: return False
    if request.method in ('GET','HEAD'): return True
    return request.headers.get('Origin','') == public_origin
