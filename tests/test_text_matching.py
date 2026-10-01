from anony_mate_api.services.text_matching import find, is_scrap


def found(needle: str, text: str) -> list[str]:
    return [text[start:end] for start, end in find(needle, text)]


def test_a_name_broken_over_a_line_or_letter_spaced_is_found():
    assert found("Hildegard Zwyssig", "Frau Hildegard\nZwyssig und Hi ldegard Zwyssig") == [
        "Hildegard\nZwyssig",
        "Hi ldegard Zwyssig",
    ]


def test_hyphens_come_and_go_between_letters():
    assert found("Basel-Landschaft", "BaselLandschaft, Basel- Landschaft, Basellandschaft") == [
        "BaselLandschaft",
        "Basel- Landschaft",
        "Basellandschaft",
    ]


def test_numbers_are_matched_exactly():
    assert found("2.45", "Abschnitte 5.2.4 5.2.5 und Wert 2.45") == ["2.45"]
    assert found("2026-0417", "Nr. 2026-0417 und 20260417") == ["2026-0417", "20260417"]


def test_accents_ligatures_and_sharp_s_are_folded():
    assert found("Strassencafe", "das Strassencafé") == ["Strassencafé"]
    assert found("Strasse", "die Straße") == ["Straße"]
    assert found("Staffelberg", "am Staﬀelberg") == ["Staﬀelberg"]


def test_a_name_stands_on_its_own():
    assert found("Basel", "Kanton Basel-Stadt") == []
    assert found("GKZ", "Organigramm_Vorlage GKZ_Entwurf.doc") == ["GKZ"]


def test_scraps():
    assert is_scrap("für")
    assert is_scrap("e e  e")
    assert is_scrap("[d]")
    assert not is_scrap("GKZ")
    assert not is_scrap("4051")
    assert not is_scrap("AG")
    assert is_scrap("für,")
    # Every word of it is a stopword; the address is not.
    assert not is_scrap("stata@bs.ch")
    assert not is_scrap("www.statistik.bs.ch")
