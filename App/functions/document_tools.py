import os
import subprocess
import json
import markdown
from pathlib import Path
import re

def create_file(data, filename, location, extension):
    """Create a file with the given data, filename, location, and extension."""
    if not filename.endswith(extension):
        filename += "." + extension
    full_path = os.path.join(location, filename)
    try:
        # Create directory if it doesn't exist
        os.makedirs(location, exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as file:
            file.write(data)
        return json.dumps({'status': 'success', 'content': f"File saved to {full_path}"})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f"Cannot save file: {str(e)}"})

def save_text(text, filename, location):
    """Save text content to a file with a custom name (appends .txt if needed)."""
    if not filename.endswith('.txt'):
        filename += '.txt'
    full_path = os.path.join(location, filename)
    try:
        # Create directory if it doesn't exist
        os.makedirs(location, exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as file:
            file.write(text)
        return json.dumps({'status': 'success', 'content': f"Text saved to {full_path}"})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f"Cannot save text: {str(e)}"})

def open_file(filename, location):
    """Open a file using the default system application."""
    full_path = os.path.join(location, filename)
    try:
        subprocess.Popen(['start', '', full_path], shell=True)
        return json.dumps({'status': 'success', 'content': f"Opened {full_path}"})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f"Cannot open file: {str(e)}"})

def delete_file(filename, location):
    """Delete a file at the given location."""
    full_path = os.path.join(location, filename)
    try:
        os.remove(full_path)
        return json.dumps({'status': 'success', 'content': f"File {full_path} deleted successfully"})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f"Cannot delete file: {str(e)}"})

def list_files(location):
    """List all files in a directory."""
    try:
        files = os.listdir(location)
        return json.dumps({'status': 'success', 'content': files})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f"Cannot list files: {str(e)}"})

def create_pdf(outputLocation: str, markdownData: str) -> str:
    """
    Convert Markdown string to PDF using fpdf2 (pure Python, no system dependencies).
    Supports tables, headings, code blocks, lists.
    
    Args:
        outputLocation: Path where PDF should be saved
        markdownData: Markdown content as string
    
    Returns:
        JSON string with confirmed status and file location or error message
    """
    try:
        from fpdf import FPDF
    except ImportError:
        return json.dumps({
            "confirmed": False,
            "error": "Missing dependency: fpdf2. Install with: pip install fpdf2 markdown"
        })
    
    # Parse markdown to HTML
    html = markdown.markdown(markdownData, extensions=['tables', 'fenced_code', 'nl2br'])
    
    # Simple HTML tag stripping
    def strip_tags(text):
        clean = re.sub(r'<br\s*/?>', '\n', text)
        clean = re.sub(r'<p>', '', clean)
        clean = re.sub(r'</p>', '\n\n', clean)
        clean = re.sub(r'<[^>]+>', '', clean)
        return clean.strip()
    
    # Extract tables
    tables = []
    table_pattern = r'<table[^>]*>(.*?)</table>'
    for table_match in re.finditer(table_pattern, html, re.DOTALL):
        table_html = table_match.group(1)
        rows = []
        row_pattern = r'<tr[^>]*>(.*?)</tr>'
        for row_match in re.finditer(row_pattern, table_html, re.DOTALL):
            row_html = row_match.group(1)
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row_html, re.DOTALL)
            rows.append([strip_tags(cell) for cell in cells])
        if rows:
            tables.append(rows)
    
    # Remove tables for text processing
    text_html = re.sub(table_pattern, '\n[TABLE_PLACEHOLDER]\n', html, flags=re.DOTALL)
    
    # Parse structure
    lines = text_html.split('\n')
    elements = []
    table_idx = 0
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if '[TABLE_PLACEHOLDER]' in line:
            if table_idx < len(tables):
                elements.append(('table', tables[table_idx]))
                table_idx += 1
        elif line.startswith('<h1'):
            text = strip_tags(line)
            elements.append(('h1', text))
        elif line.startswith('<h2'):
            text = strip_tags(line)
            elements.append(('h2', text))
        elif line.startswith('<h3'):
            text = strip_tags(line)
            elements.append(('h3', text))
        elif line.startswith('<pre><code'):
            code = re.search(r'<code[^>]*>(.*?)</code>', line, re.DOTALL)
            if code:
                elements.append(('code', strip_tags(code.group(1))))
        elif line.startswith('<ul') or line.startswith('<ol'):
            items = re.findall(r'<li[^>]*>(.*?)</li>', line, re.DOTALL)
            elements.append(('list', [strip_tags(item) for item in items]))
        elif line.startswith('<blockquote'):
            text = strip_tags(line)
            elements.append(('quote', text))
        else:
            text = strip_tags(line)
            if text:
                elements.append(('text', text))
    
    # Create PDF with built-in fonts only
    try:
        output_path = Path(outputLocation)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
    except Exception as e:
        return json.dumps({
            "confirmed": False,
            "error": f"PDF initialization failed: {str(e)}"
        })
    
    # Render elements using only standard built-in fonts
    for elem_type, content in elements:
        if elem_type == 'h1':
            pdf.set_font('Helvetica', 'B', 20)
            pdf.ln(10)
            # Handle unicode by replacing non-latin chars
            safe_text = content.encode('latin-1', 'replace').decode('latin-1')[:100]
            pdf.cell(0, 10, txt=safe_text, ln=True)
            pdf.ln(5)
            
        elif elem_type == 'h2':
            pdf.set_font('Helvetica', 'B', 16)
            pdf.ln(8)
            safe_text = content.encode('latin-1', 'replace').decode('latin-1')[:100]
            pdf.cell(0, 8, txt=safe_text, ln=True)
            pdf.ln(3)
            
        elif elem_type == 'h3':
            pdf.set_font('Helvetica', 'B', 13)
            pdf.ln(6)
            safe_text = content.encode('latin-1', 'replace').decode('latin-1')[:100]
            pdf.cell(0, 6, txt=safe_text, ln=True)
            
        elif elem_type == 'code':
            pdf.set_font('Courier', '', 9)
            pdf.set_fill_color(240, 240, 240)
            safe_code = content.encode('latin-1', 'replace').decode('latin-1')[:2000]
            pdf.multi_cell(0, 5, txt=safe_code, fill=True)
            pdf.ln(3)
            pdf.set_fill_color(255, 255, 255)
            
        elif elem_type == 'list':
            pdf.set_font('Helvetica', '', 11)
            for item in content:
                pdf.cell(10)
                safe_item = item.encode('latin-1', 'replace').decode('latin-1')[:200]
                pdf.cell(0, 6, txt=f"- {safe_item}", ln=True)
            pdf.ln(2)
            
        elif elem_type == 'quote':
            pdf.set_font('Helvetica', 'I', 11)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(10)
            safe_quote = content.encode('latin-1', 'replace').decode('latin-1')[:500]
            pdf.multi_cell(0, 6, txt=safe_quote)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(3)
            
        elif elem_type == 'table':
            if not content:
                continue
            pdf.set_font('Helvetica', '', 9)
            
            # Calculate column widths
            if content:
                num_cols = len(content[0])
                page_width = pdf.w - 2 * pdf.l_margin
                col_width = page_width / num_cols
                col_widths = [col_width] * num_cols
            
            # Header row
            pdf.set_fill_color(52, 73, 94)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font('Helvetica', 'B', 9)
            for i, cell in enumerate(content[0]):
                safe_cell = str(cell).encode('latin-1', 'replace').decode('latin-1')[:20]
                pdf.cell(col_widths[i], 8, txt=safe_cell, border=1, fill=True)
            pdf.ln()
            
            # Data rows
            pdf.set_fill_color(248, 249, 250)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font('Helvetica', '', 9)
            for row_idx, row in enumerate(content[1:]):
                for i, cell in enumerate(row):
                    fill = (row_idx % 2 == 0)
                    safe_cell = str(cell).encode('latin-1', 'replace').decode('latin-1')[:20]
                    pdf.cell(col_widths[i], 7, txt=safe_cell, border=1, fill=fill)
                pdf.ln()
            pdf.ln(5)
            
        else:  # text
            pdf.set_font('Helvetica', '', 11)
            safe_text = content.encode('latin-1', 'replace').decode('latin-1')[:2000]
            pdf.multi_cell(0, 6, txt=safe_text)
            pdf.ln(2)
    
    try:
        pdf.output(str(output_path))
    except Exception as e:
        return json.dumps({
            "confirmed": False,
            "error": f"PDF save failed: {str(e)}"
        })
    
    if not output_path.exists():
        return json.dumps({
            "confirmed": False,
            "error": "PDF file was not created"
        })
    
    return json.dumps({
        "confirmed": True,
        "location": str(output_path.resolve())
    })

# Tool definition
TOOL_DEFINITION = {
 "type": "function",
 "function": {
     "name": "create_pdf",
     "description": "Convert Markdown content to PDF with support for tables, headings, code blocks, and lists. Pure Python implementation with no system dependencies.",
     "parameters": {
         "type": "object",
         "properties": {
             "markdownData": {
                 "type": "string",
                 "description": "Markdown content as string. Supports: headings (# ## ###), tables (| col |), code blocks (```), bullet lists (- item), numbered lists (1. item), bold (**text**), italic (*text*)"
             },
             "outputLocation": {
                 "type": "string",
                 "description": "Full file path for output PDF (e.g., 'report.pdf' or '/path/to/file.pdf')"
             }
         },
         "required": ["outputLocation", "markdownData"]
     }
 }
}
