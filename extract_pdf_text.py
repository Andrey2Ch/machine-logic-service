from pdfminer.high_level import extract_text
from pathlib import Path


def main() -> None:
	pdf_path = Path(r"C:\Projects\TG_bot\Text2SQL_R77_AI.pdf")
	out_path = pdf_path.with_suffix(".txt")
	text = extract_text(str(pdf_path))
	out_path.write_text(text, encoding="utf-8")
	print(out_path)


if __name__ == "__main__":
	main()



