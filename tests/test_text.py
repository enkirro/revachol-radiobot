import unittest

from revachol_radiobot.text import MAX_WEIGHTED_LENGTH, normalize, split_thread, weighted_length


class WeightedLengthTests(unittest.TestCase):
    def test_latin_counts_one(self):
        self.assertEqual(weighted_length("Kim Kitsuragi: «Bien», ñandú"), 28)

    def test_emoji_and_cjk_count_two(self):
        self.assertEqual(weighted_length("🙂"), 2)
        self.assertEqual(weighted_length("漢字"), 4)

    def test_url_counts_23(self):
        self.assertEqual(weighted_length("mira https://example.com/una/ruta/larguisima"), 5 + 23)

    def test_typographic_quotes_count_one(self):
        # “ ” — pesan 1; la elipsis … (U+2026) queda fuera de los rangos ligeros y pesa 2.
        self.assertEqual(weighted_length("“hola” — …"), 11)


class SplitTests(unittest.TestCase):
    def test_short_text_single_part(self):
        self.assertEqual(split_thread("Tú: \"Tiene sentido\"."), ['Tú: "Tiene sentido".'])

    def test_normalizes_whitespace(self):
        self.assertEqual(normalize("  a \n\t b  "), "a b")

    def test_long_text_is_numbered_and_fits(self):
        text = " ".join(f"Frase número {i} del diálogo." for i in range(60))
        parts = split_thread(text)
        self.assertGreater(len(parts), 1)
        for i, part in enumerate(parts, start=1):
            self.assertTrue(part.endswith(f"({i}/{len(parts)})"), part)
            self.assertLessEqual(weighted_length(part), MAX_WEIGHTED_LENGTH)

    def test_no_words_are_lost(self):
        text = " ".join(f"palabra{i}" for i in range(200))
        parts = split_thread(text, numbering=False)
        self.assertEqual(" ".join(parts).split(), text.split())

    def test_prefers_sentence_boundaries(self):
        first = "A" * 10 + " " + "b " * 100 + "fin de frase."
        text = first + " " + "Segunda frase " * 20
        parts = split_thread(text, numbering=False)
        self.assertTrue(parts[0].endswith("fin de frase."), parts[0])

    def test_never_cuts_inside_a_sentence_when_possible(self):
        s1 = "Kim: " + "uno dos tres, " * 12 + "fin."  # ~170
        s2 = '"' + "cuatro cinco seis, " * 8 + 'fin." El teniente asiente.'  # ~180
        parts = split_thread(f"{s1} {s2}", max_parts=2)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0], f"{s1} (1/2)")
        self.assertEqual(parts[1], f"{s2} (2/2)")

    def test_closing_quote_stays_with_its_sentence(self):
        s1 = '"' + "palabra " * 25 + 'final".'
        s2 = "Te mira. " * 20
        parts = split_thread(f"{s1} {s2}", numbering=False)
        self.assertIn('final".', parts[0])
        self.assertTrue(parts[0].endswith("."), parts[0])

    def test_falls_back_to_clauses_to_respect_max_parts(self):
        # Tres frases de ~150: solo entre frases saldrían 3 tuits; con comas, 2.
        sentence = "Esto es una frase larga, " + "con muchas palabras " * 6 + "y punto."
        text = " ".join([sentence] * 3)
        self.assertEqual(len(split_thread(text, max_parts=None)), 3)
        parts = split_thread(text, max_parts=2)
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(weighted_length(p) <= MAX_WEIGHTED_LENGTH for p in parts))

    def test_giant_word_is_hard_split(self):
        parts = split_thread("x" * 700, numbering=False)
        self.assertEqual("".join(parts), "x" * 700)
        self.assertTrue(all(weighted_length(p) <= MAX_WEIGHTED_LENGTH for p in parts))

    def test_empty(self):
        self.assertEqual(split_thread("   "), [])


if __name__ == "__main__":
    unittest.main()
