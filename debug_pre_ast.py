from pathlib import Path
import textwrap

text = Path('/home/user/fmsecure_refresh/main.py').read_text(encoding='utf-8').replace('\r\n', '\n')
text = text.replace(
    'from fastapi.staticfiles import StaticFiles\n',
    'from fastapi.staticfiles import StaticFiles\nfrom fastapi.templating import Jinja2Templates\n',
    1,
)
mount_block = textwrap.dedent('''
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass
''')
helper_block = textwrap.dedent('''
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass

def _nl2br(value):
    if not value:
        return ""
    return str(value).replace("\\n", "<br>")
''')
text = text.replace(mount_block, helper_block, 1)
lines = text.split('\n')
for i in range(320, 345):
    print(f'{i+1}: {lines[i]}')
