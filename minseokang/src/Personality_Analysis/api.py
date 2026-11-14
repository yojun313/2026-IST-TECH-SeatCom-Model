from extractor import NameExtractor

class PersonalityAnalysis():
    def __init__(self):
        self.name_extractor = NameExtractor()
    def analyze_by_GPT_API(self, source):
        ex_text = self.name_extractor.extract(source)
