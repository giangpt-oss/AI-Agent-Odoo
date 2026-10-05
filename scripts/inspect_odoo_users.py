import sys
import asyncio
from pathlib import Path

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.connectors.odoo.client import OdooAsyncClient
from app.core.config import get_settings

async def main():
    settings = get_settings()
    odoo = OdooAsyncClient(
        base_url=settings.ODOO_URL,
        db=settings.ODOO_DB,
        username=settings.odoo_user,
        api_key=settings.ODOO_API_KEY
    )
    await odoo.authenticate()
    
    # Check fields of res.users
    fields = await odoo.execute_kw(
        model='res.users',
        method='fields_get',
        args=[[], ['name', 'type', 'string']]
    )
    role_fields = [k for k in fields.keys() if any(x in k.lower() for x in ['role', 'group', 'sale', 'admin', 'hr', 'perm'])]
    print("Relevant res.users fields:", role_fields[:20])

    # Check hr.employee fields
    emp_fields = await odoo.execute_kw(
        model='hr.employee',
        method='fields_get',
        args=[[], ['name', 'type', 'string']]
    )
    print("Relevant hr.employee fields:", [k for k in emp_fields.keys() if any(x in k.lower() for x in ['job', 'dept', 'role', 'title', 'user', 'manager', 'telegram', 'phone'])])

if __name__ == '__main__':
    asyncio.run(main())
