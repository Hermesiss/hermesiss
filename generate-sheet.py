TEMPLATE_FILE = 'sheet-template.svg'
OUTPUT_FILE = 'sheet.svg'

with open(TEMPLATE_FILE, 'r', encoding='utf-8') as f:
    template = f.read()

with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    f.write(template)
