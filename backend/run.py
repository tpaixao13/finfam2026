"""Sobe a API em modo desenvolvimento: python run.py  (docs em http://127.0.0.1:8000/api/docs)"""
import os

import uvicorn

if __name__ == '__main__':
    production = os.environ.get('FINFAM_ENV', 'development') == 'production'
    uvicorn.run('app.main:app', host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', 8000)),
                reload=not production, proxy_headers=True)
