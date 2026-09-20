# utils/ascii_helper.py
import re


class AsciiHelper:
    """Helper class to format ANSI messages for Discord"""
    
    @staticmethod
    def _clean_empty_lines(content: str) -> str:
        """Remove lines that only contain '> ' and nothing else"""
        lines = content.split('\n')
        cleaned_lines = []
        for line in lines:
            if re.match(r'^>\s*$', line):
                continue
            cleaned_lines.append(line)
        return '\n'.join(cleaned_lines)
    
    @staticmethod
    def format(message, username=None):
        """Format a message with ANSI colors inside a codeblock"""
        ansi_content = f"\x1b[2;37m| {message} |\x1b[0m"
        result = "> ```ansi\n"
        for line in ansi_content.split('\n'):
            result += f"> {line}\n"
        result += "> ```"
        return AsciiHelper._clean_empty_lines(result)
    
    @staticmethod
    def multiline(lines, username=None, raw=False, cmd_count=None, page_info=None):
        """Format multiple lines of text with ANSI colors"""
        if raw:
            result = "> ```ansi\n"
            for line in lines:
                if line.strip() == "":
                    result += "> \n"
                else:
                    result += f"> {line}\n"
            result += "> ```"
            return AsciiHelper._clean_empty_lines(result)
        
        ansi_lines = []
        for line in lines:
            if line.strip() == "":
                ansi_lines.append("\x1b[2;37m|  |\x1b[0m")
            else:
                ansi_lines.append(f"\x1b[2;37m| {line} |\x1b[0m")
        result = "> ```ansi\n"
        for line in ansi_lines:
            result += f"> {line}\n"
        result += "> ```"
        return AsciiHelper._clean_empty_lines(result)
    
    @staticmethod
    def info(message):
        result = f"> ```ansi\n> \x1b[2;37m[Authd?] {message}\x1b[0m\n> ```"
        return AsciiHelper._clean_empty_lines(result)
    
    @staticmethod
    def success(message):
        result = f"> ```ansi\n> \x1b[2;37m[Authd] {message}\x1b[0m\n> ```"
        return AsciiHelper._clean_empty_lines(result)
    
    @staticmethod
    def error(message):
        result = f"> ```ansi\n> \x1b[2;37m[Authd - error] {message}\x1b[0m\n> ```"
        return AsciiHelper._clean_empty_lines(result)
    
    @staticmethod
    def warning(message):
        result = f"> ```ansi\n> \x1b[2;37m[Authd :-:] {message}\x1b[0m\n> ```"
        return AsciiHelper._clean_empty_lines(result)
    
    @staticmethod
    def custom(message, color_code="2;37m"):
        result = f"> ```ansi\n> \x1b[{color_code}{message}\x1b[0m\n> ```"
        return AsciiHelper._clean_empty_lines(result)
    
    @staticmethod
    def table(data, headers=None):
        if not data:
            return AsciiHelper.warning("No data to display")
        if headers is None:
            if isinstance(data[0], dict):
                headers = list(data[0].keys())
            else:
                headers = [f"Column {i+1}" for i in range(len(data[0]))]
        rows = []
        for item in data:
            if isinstance(item, dict):
                rows.append([str(item.get(h, '')) for h in headers])
            else:
                rows.append([str(x) for x in item])
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                col_widths[i] = max(col_widths[i], len(cell))
        result = "> ```ansi\n"
        header_line = ""
        for i, header in enumerate(headers):
            header_line += f"\x1b[2;37m{header:<{col_widths[i]}}\x1b[0m  "
        result += f"> {header_line}\n"
        sep_line = ""
        for width in col_widths:
            sep_line += f"\x1b[2;37m{'-' * width}\x1b[0m  "
        result += f"> {sep_line}\n"
        for row in rows:
            row_line = ""
            for i, cell in enumerate(row):
                row_line += f"\x1b[2;37m{cell:<{col_widths[i]}}\x1b[0m  "
            result += f"> {row_line}\n"
        result += "> ```"
        return AsciiHelper._clean_empty_lines(result)


ascii = AsciiHelper()
