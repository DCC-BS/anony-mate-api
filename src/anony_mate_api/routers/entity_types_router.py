from enum import Enum

from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import inject
from fastapi import APIRouter
from pydantic import BaseModel

logger = get_logger("entity_types_router")


class EntityType(Enum):
    default = "default"
    legal = "legal"
    full = "full"


class EntityTypeInfo(BaseModel):
    """One entity type, as the detection model and the reader each need it."""

    #: What the type is called in the interface, and in a replacement written
    #: as ``{name}-{subject}``. German, because the documents are.
    name: str
    #: What the label means, in the words the detection model reads.
    #:
    #: Not documentation: it is sent to the model with the text and is most of
    #: what the model has to go on. A thin one costs recall, and on a dense
    #: document costs it silently — a chunk comes back with nothing rather than
    #: with less.
    description: str


#: Every entity type the presets are drawn from.
#:
#: Label names and descriptions are German because the documents are. Measured
#: over the ten long documents of the detection benchmark, the same descriptions
#: under English label names scored f1 0.894 against 0.924, with 346 false
#: positives against 204: the label name anchors the model as much as the
#: description does. Both come from that benchmark, so a change here should be
#: measured there.
ENTITY_TYPES: dict[str, EntityTypeInfo] = {
    "person": EntityTypeInfo(
        name="Person",
        description=(
            "Name einer natürlichen Person, auch abgekürzt, verdreht oder fehlerhaft geschrieben: Vor- "
            "und Nachname z.B. 'Sandra Bircher', umgekehrt mit Komma z.B. 'Bircher, Sandra', abgekürzter "
            "Vorname als einzelner Buchstabe mit oder ohne Punkt plus Nachname z.B. 'R. Holzer' oder 'R "
            "Holzer', sowie Nachname allein z.B. 'gemäss Holzer'. Ein Nachname nach einer Anrede oder einem Titel ist immer zu erfassen, z.B. 'Mueller' in 'Herr Mueller', 'Holzer' in 'Frau Holzer', 'Kaeufer' in 'Frau Dr. Kaeufer', 'Bircher' in 'Herrn Bircher' — auch wenn kein Vorname dabei steht und der Nachname sonst wie ein gewöhnliches Wort aussieht. Auch wenn der Name "
            "Tippfehler enthält, klein geschrieben ist oder ohne Leerzeichen am vorangehenden Wort klebt. "
            "Auch Namen in Unterschriftenblöcken und Absenderzeilen. Titel und Anreden wie Frau, Herr, "
            "Dr. gehören nicht zur Fundstelle. Keine Rollen- oder Funktionsbezeichnungen wie Klientin, "
            "Patientin, Klägerin, Gesuchstellerin, Mutter, Vater, Beistandsperson, Sachbearbeiter — auch "
            "dann nicht, wenn sie für eine bestimmte Person stehen."
        ),
    ),
    "organisation": EntityTypeInfo(
        name="Organisation",
        description=(
            "Eigenname einer Behörde, Firma, Klinik, Schule oder Versicherung, z.B. 'Sozialhilfe Basel- "
            "Stadt', 'Bäckerei Sutter GmbH'. Immer den vollständigen Namen erfassen, einschliesslich "
            "Rechtsform und Zusatz wie 'AG', 'GmbH', 'in Liquidation', und einschliesslich eines "
            "Personennamens, der Teil des Firmennamens ist, z.B. 'Malerei Kuster, Inhaberin R. Holzer'. "
            "Auch Marken- und Fantasienamen, die nicht wie ein Firmenname aussehen. Ebenso der Eigenname "
            "eines Fachsystems, einer Applikation oder eines Registers, z.B. 'gemäss E Domus'. Keine "
            "Gattungsbegriffe wie Krankenkasse, Arbeitgeber, Firma, Behörde und keine "
            "Dokumentüberschriften."
        ),
    ),
    "adresse": EntityTypeInfo(
        name="Adresse",
        description=(
            "Ortsangabe auf Strassenebene, in allen Landessprachen: Strassenname mit oder ohne "
            "Hausnummer, optional mit PLZ und Ort, z.B. 'Dornacherstrasse 112, 4053 Basel', 'Via Serafino "
            "5, 6600 Locarno', 'Chemin des Vignes 4a, 1010 Lausanne', 'Route de Berne 12'. Auch Flur- und "
            "Ortslagen ohne Strassen-Suffix, z.B. 'In den Reben 12', 'in Canaa 8'. Immer die längste "
            "zusammenhängende Angabe erfassen, inklusive PLZ und Ort wenn vorhanden."
        ),
    ),
    "ort": EntityTypeInfo(
        name="Ort",
        description=(
            "Name einer Gemeinde, Stadt, eines Quartiers oder Landes, in allen Landessprachen, z.B. 'von "
            "Muttenz', 'in Riehen', 'Ependes (VD)', 'Cugnasco-Gerra', 'Libanon'. Der Ortsname direkt nach "
            "einer vierstelligen Postleitzahl ist immer zu erfassen, z.B. 'Muttenz' in '4127 Muttenz', "
            "'Basel' in '4057 Basel', 'Zuerich' in '8001 Zuerich' — auch wenn davor eine Strasse steht "
            "und auch wenn danach ein Komma folgt. Nur der Ortsname gehoert zur Fundstelle, weder die "
            "Postleitzahl noch der Strassenname. Ein nachgestelltes Kantonskuerzel in Klammern gehoert "
            "zur Fundstelle. Nicht im Namen einer Behoerde wie 'Sozialhilfe Basel-Stadt', kein "
            "Funkrufname, nicht das Wort 'Ort' selbst und nicht 'Schweiz' in allgemeinen Wendungen wie "
            "'in der Schweiz'."
        ),
    ),
    "geburtsdatum": EntityTypeInfo(
        name="Geburtsdatum",
        description=("Datum, an dem eine Person geboren wurde, meist eingeleitet durch 'geboren am'."),
    ),
    "datum": EntityTypeInfo(
        name="Datum",
        description=(
            "Kalenderdatum eines Ereignisses, das kein Geburtsdatum ist, z.B. 'ab 3. Februar 2026', "
            "'17.09.2026'. Eine alleinstehende Jahreszahl ohne Tag und Monat ist kein Datum."
        ),
    ),
    "geldbetrag": EntityTypeInfo(
        name="Geldbetrag",
        description=(
            "Geldbetrag aus Währung und Zahl, in beliebiger Währung und Schreibweise, z.B. 'CHF 8'400', "
            "'Fr. 1250.--', '2'450.00 Franken', 'EUR 1.250,00', '1250 Euro', 'USD 3,200.50', '$ 750', "
            "'€ 99.90', 'GBP 40'. Währungskürzel oder Währungszeichen und Zahl gehören zusammen zur "
            "Fundstelle, ebenso ein nachgestelltes '.--' oder '.00'. Auch ein Betrag ohne Währung, wenn aus "
            "dem Satz hervorgeht, dass es um Geld geht, z.B. 'Schaden von rund 8'400', 'Lohn von 4'800 pro "
            "Monat'. Keine Prozentangaben, keine Anzahl Monatslöhne, keine Jahreszahlen und keine "
            "Postleitzahlen."
        ),
    ),
    "telefonnummer": EntityTypeInfo(
        name="Telefonnummer",
        description=("Rufnummer aus Ziffern, z.B. '061 267 44 12'. Nicht die Wörter Telefon oder Telefonat."),
    ),
    "e-mail-adresse": EntityTypeInfo(
        name="E-Mail-Adresse",
        description=("E-Mail-Adresse mit @-Zeichen."),
    ),
    "iban": EntityTypeInfo(
        name="IBAN",
        description=("Schweizer Kontonummer im IBAN-Format, beginnt mit CH."),
    ),
    "ahv-nummer": EntityTypeInfo(
        name="AHV-Nummer",
        description=(
            "Schweizer Sozialversicherungsnummer im Format 756.XXXX.XXXX.XX. Keine Gesetzesartikel, keine "
            "Serien- oder IMEI-Nummern und keine Dienstkennungen."
        ),
    ),
    "aktenzeichen": EntityTypeInfo(
        name="Aktenzeichen",
        description=(
            "Dossier-, Geschäfts-, Verfahrens- oder Verfügungsnummer einer Behörde, z.B. 'KESB-BS "
            "2026/4187'. Keine Dienstkennungen von Einsatzkräften oder Fahrzeugen und keine Seriennummern "
            "von Gegenständen."
        ),
    ),
    "dienstkennung": EntityTypeInfo(
        name="Dienstkennung",
        description=(
            "Interne Kennung einer Polizeiressource, enthält immer eine Zahl: Funkrufname eines "
            "Einsatzfahrzeugs, z.B. 'Cassiopeia 12', oder Dienst-, Personal- oder Patrouillennummer einer "
            "Einsatzkraft, z.B. 'Dienstnummer K19', 'Spitzbub B40'. Meist ein Buchstabenkürzel mit Zahl. "
            "Keine Rang- oder Funktionsbezeichnungen wie Wachtmeister, Feldweibel, Detektiv und keine "
            "Namen von Diensten oder Abteilungen wie Einsatzzentrale, Forensik, Fahndung. Keine Dossier- "
            "oder Verfahrensnummern, keine AHV-Nummern, keine Seriennummern von Gegenständen."
        ),
    ),
    "seriennummer": EntityTypeInfo(
        name="Seriennummer",
        description=(
            "Serien-, IMEI- oder Gerätenummer eines Gegenstands, meist eine lange Folge aus Ziffern und "
            "Buchstaben, z.B. 'Seriennummer 77B10299111CCC'. Auch wenn sie ausgesprochen diktiert ist, "
            "z.B. '77 B 102 9 9111 klein ccc'. Keine AHV-Nummern, keine Dienstkennungen, keine "
            "Geldbeträge."
        ),
    ),
    "ip-adresse": EntityTypeInfo(
        name="IP-Adresse",
        description=(
            "IP-Adresse eines Geräts, z.B. '203.0.113.47' oder eine IPv6-Adresse wie '2001:db8::7334'. "
            "Nicht die blosse Erwähnung des Worts IP-Adresse ohne Wert."
        ),
    ),
    "mac-adresse": EntityTypeInfo(
        name="MAC-Adresse",
        description=(
            "MAC- oder Hardware-Adresse eines Netzwerkgeräts, sechs Bytes in Hexadezimal, z.B. "
            "'00:1B:44:11:3A:B7'. Nicht die blosse Erwähnung des Worts MAC-Adresse ohne Wert."
        ),
    ),
    "kfz-kennzeichen": EntityTypeInfo(
        name="Kontrollschild",
        description=(
            "Schweizer Fahrzeugkontrollschild aus Kantonskürzel und Nummer, z.B. 'BS 12345', 'BL 4711'. "
            "Auch ausländische Kennzeichen. Keine Fahrgestell- oder Seriennummern und keine Funkrufnamen "
            "von Einsatzfahrzeugen."
        ),
    ),
    "ausweisnummer": EntityTypeInfo(
        name="Ausweisnummer",
        description=(
            "Nummer eines Identitätsdokuments: Pass-, Identitätskarten- oder Ausländerausweisnummer, z.B. "
            "'X1234567', 'Ausweis B, Nr. 78542136'. Keine AHV-Nummern, keine Versichertennummern, keine "
            "Dossiernummern."
        ),
    ),
    "versichertennummer": EntityTypeInfo(
        name="Versichertennummer",
        description=(
            "Versicherten-, Policen- oder Vertragsnummer einer Kranken-, Unfall- oder Sachversicherung, "
            "z.B. 'Policen-Nr. 4711.8890.22'. Keine AHV-Nummern."
        ),
    ),
    "patientennummer": EntityTypeInfo(
        name="Patientennummer",
        description=(
            "Patienten-, Fall- oder Behandlungsnummer einer Klinik oder Praxis, z.B. 'Patient-Nr. "
            "884320', 'Fall-ID 2026-11284'. Keine Dossiernummern einer Behörde."
        ),
    ),
    "uid-nummer": EntityTypeInfo(
        name="UID-Nummer",
        description=(
            "Schweizer Unternehmens-Identifikationsnummer im Format CHE-123.456.789, auch mit dem Zusatz "
            "MWST. Keine AHV-Nummern."
        ),
    ),
    "beruf": EntityTypeInfo(
        name="Beruf",
        description=(
            "Ausgeübter Beruf oder Ausbildungsberuf einer natürlichen Person, z.B. 'gelernte "
            "Pflegefachfrau', 'Coiffeur', 'Lastwagenchauffeur'. Nicht die Rolle im Verfahren wie "
            "Gesuchsteller, Beistand, Sachbearbeiterin, und keine Funktion innerhalb einer Behörde."
        ),
    ),
    "nationalitaet": EntityTypeInfo(
        name="Nationalität",
        description=(
            "Staatsangehörigkeit oder Herkunft einer Person, z.B. 'kosovarische Staatsangehörige', "
            "'Schweizer', 'portugiesischer Staatsbürger'. Auch dann, wenn die Angabe in einem "
            "französisch-, italienisch- oder englischsprachigen Satz steht, z.B. 'Nationalité: deutsche "
            "Staatsangehörige'. Nicht der blosse Ländername ohne Personenbezug."
        ),
    ),
    "gesundheitsangabe": EntityTypeInfo(
        name="Gesundheitsangabe",
        description=(
            "Konkrete gesundheitsbezogene Angabe zu einer bestimmten Person: benannte Diagnose, Befund, "
            "Medikament oder Suchterkrankung, z.B. 'Diabetes mellitus Typ 2', 'ADHS', 'Gonarthrose "
            "beidseits', 'Migräne mit Aura'. Nur die Angabe selbst, nicht den ganzen Satz. Keine "
            "allgemeinen Erwähnungen von Gesundheit, Therapie, Arztbesuch, Krankschreibung oder Pflege "
            "ohne benannten Befund und keine Namen von Kliniken oder Fachrichtungen."
        ),
    ),
    "url": EntityTypeInfo(
        name="URL",
        description=(
            "Web-Adresse mit Domain, z.B. 'www.example.ch' oder 'https://portal.bs.ch/dossier'. Keine E-Mail-Adressen."
        ),
    ),
    "benutzername": EntityTypeInfo(
        name="Benutzername",
        description=(
            "Benutzerkonto, Login oder Profilname einer Person, z.B. 'Benutzer m.keller', '@rico_84' auf "
            "Instagram. Keine E-Mail-Adressen und keine Klarnamen."
        ),
    ),
    "plz": EntityTypeInfo(
        name="PLZ",
        description=(
            "Schweizer Postleitzahl: die vierstellige Zahl unmittelbar vor einem Ortsnamen, z.B. '4127' "
            "in '4127 Muttenz', '4057' in '4057 Basel', '8001' in '8001 Zuerich'. Nur die vier Ziffern "
            "gehoeren zur Fundstelle, der Ortsname selbst nicht. Keine Jahreszahl wie '2024' oder '1971', "
            "keine Hausnummer, kein Geldbetrag, keine Dossier- oder Geschaeftsnummer und keine "
            "Telefonnummer."
        ),
    ),
}


#: Which types each preset detects.
PRESETS: dict[EntityType, list[str]] = {
    EntityType.default: ["person", "ort"],
    EntityType.legal: ["person", "ort", "plz", "organisation", "datum", "telefonnummer"],
    EntityType.full: list(ENTITY_TYPES),
}


@inject
def create_router() -> APIRouter:
    logger.info("Creating entity types router")
    router: APIRouter = APIRouter(prefix="/entity_types")

    @router.get("/{type_name}")
    async def entity_types(type_name: EntityType) -> dict[str, EntityTypeInfo]:
        return {label: ENTITY_TYPES[label] for label in PRESETS[type_name]}

    logger.info("entity types router configured")

    return router
