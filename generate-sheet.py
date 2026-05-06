import requests
import re

TEMPLATE_FILE = 'sheet-template.svg'
OUTPUT_FILE = 'sheet.svg'
TROPHY_URL = (
    'https://github-profile-trophy.vercel.app/?username=Hermesiss'
    '&theme=flat&no-frame=true&no-bg=true&margin-w=6'
    '&title=Stars,Commits,Experience,Repositories,MultipleLang,PullRequest&column=3'
)
GRAPH_URL = (
    'https://github-readme-activity-graph.vercel.app/graph?username=Hermesiss'
    '&theme=github&bg_color=fafaf9&color=383838&line=0a66c2&point=666666'
    '&area_color=0a66c2&area=true&height=240'
)

with open(TEMPLATE_FILE, 'r', encoding='utf-8') as f:
    template = f.read()

trophy_svg = requests.get(TROPHY_URL).text
# Remove XML declaration if present
if trophy_svg.startswith('<?xml'):
    trophy_svg = trophy_svg.split('?>', 1)[-1].strip()

graph_svg = requests.get(GRAPH_URL).text
if graph_svg.startswith('<?xml'):
    graph_svg = graph_svg.split('?>', 1)[-1].strip()

# Replace the <svg ...> tag with correct width, height, and viewBox
# Remove any existing width, height, viewBox attributes
svg_tag_pattern = r'<svg[^>]*>'
# Display size: wide graph, ~240px API height → scale to fit card (~656px)
new_svg_tag = '<svg width="656" height="188" viewBox="0 0 1200 300"'
# Keep any other attributes (like xmlns, fill, etc.)
match = re.search(svg_tag_pattern, graph_svg)
if match:
    # Extract other attributes except width, height, viewBox
    attrs = re.sub(r'(width|height|viewBox)="[^"]*"', '', match.group(0))
    # Remove <svg and >
    attrs = attrs[len('<svg'):].rstrip('>')
    graph_svg = re.sub(svg_tag_pattern, f'{new_svg_tag}{attrs}>', graph_svg, count=1)

result = template.replace('%%github-profile-trophy%%', trophy_svg)
result = result.replace('%%github-readme-activity-graph%%', graph_svg)

with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    f.write(result) 