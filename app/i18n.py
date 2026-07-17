"""UI language support (English, French, Spanish, German, Portuguese)."""

from __future__ import annotations

from .settings import read_settings, update_settings

# BCP 47-ish codes used in settings and menus
LANG_EN = "en"
LANG_FR = "fr"
LANG_ES = "es"
LANG_DE = "de"
LANG_PT = "pt"

LANGUAGES: dict[str, str] = {
    LANG_EN: "English",
    LANG_FR: "Français",
    LANG_ES: "Español",
    LANG_DE: "Deutsch",
    LANG_PT: "Português",
}

DEFAULT_LANGUAGE = LANG_EN

# English msgid → translation. Missing keys fall back to English.
_TRANSLATIONS: dict[str, dict[str, str]] = {
    LANG_FR: {
        "eBraille Checker": "eBraille Checker",
        "eBraille Checker GUI": "eBraille Checker GUI",
        "Publication": "Publication",
        "Path:": "Chemin :",
        "Select or drop a .ebrl file or folder — checking starts automatically": (
            "Sélectionnez ou déposez un fichier .ebrl ou un dossier — "
            "la vérification démarre automatiquement"
        ),
        "Select &file…": "Sélectionner un &fichier…",
        "Select file": "Sélectionner un fichier",
        "Select a packaged publication (Ctrl+O)": (
            "Sélectionner une publication empaquetée (Ctrl+O)"
        ),
        "Select f&older…": "Sélectionner un d&ossier…",
        "Select folder": "Sélectionner un dossier",
        "Select an exploded publication folder (Ctrl+Shift+O)": (
            "Sélectionner un dossier de publication décompressé (Ctrl+Shift+O)"
        ),
        "Result": "Résultat",
        "Check result": "Résultat de la vérification",
        "No check run yet.": "Aucune vérification effectuée.",
        "Checking…": "Vérification…",
        "Issues": "Problèmes",
        "Filter:": "Filtre :",
        "Issue filter": "Filtre des problèmes",
        "All issues": "Tous les problèmes",
        "Errors only": "Erreurs uniquement",
        "Warnings only": "Avertissements uniquement",
        "Info / usage": "Info / usage",
        "&Copy summary": "&Copier le résumé",
        "Copy the result summary (Ctrl+Shift+C)": (
            "Copier le résumé du résultat (Ctrl+Shift+C)"
        ),
        "&Save report…": "&Enregistrer le rapport…",
        "Save the report to a file (Ctrl+S)": (
            "Enregistrer le rapport dans un fichier (Ctrl+S)"
        ),
        "Issues list": "Liste des problèmes",
        "Severity": "Sévérité",
        "Code": "Code",
        "Location": "Emplacement",
        "Message": "Message",
        "Show full &log": "Afficher le journal &complet",
        "Hide full &log": "Masquer le journal &complet",
        "Show or hide full log": "Afficher ou masquer le journal complet",
        "Show or hide the full checker log (Ctrl+L)": (
            "Afficher ou masquer le journal complet (Ctrl+L)"
        ),
        "Full checker log": "Journal complet",
        "&File": "&Fichier",
        "Select &file…\tCtrl+O": "Sélectionner un &fichier…\tCtrl+O",
        "Select f&older…\tCtrl+Shift+O": "Sélectionner un d&ossier…\tCtrl+Shift+O",
        "&Save report…\tCtrl+S": "&Enregistrer le rapport…\tCtrl+S",
        "E&xit\tEsc": "&Quitter\tÉchap",
        "&Edit": "&Édition",
        "&Copy summary\tCtrl+Shift+C": "&Copier le résumé\tCtrl+Shift+C",
        "C&lear results\tCtrl+Shift+N": "&Effacer les résultats\tCtrl+Shift+N",
        "A check is already running. Wait for it to finish, then clear.": (
            "Une vérification est déjà en cours. Attendez qu’elle se termine, "
            "puis effacez."
        ),
        "&Tools": "&Outils",
        "&Re-check publication\tF5": "&Revérifier la publication\tF5",
        "Show/hide full &log\tCtrl+L": "Afficher/masquer le journal &complet\tCtrl+L",
        "Check for &updates…": "Rechercher des &mises à jour…",
        "&Download / reinstall checker…": "&Télécharger / réinstaller le vérificateur…",
        "&Language": "&Langue",
        "&Help": "&Aide",
        "&About": "À &propos",
        "Starting…": "Démarrage…",
        "Ready": "Prêt",
        "Java required": "Java requis",
        "Java was not found.\n\n"
        "If you are running from source, install a Java Runtime "
        "(JRE 17 or newer recommended) and ensure java is on your PATH.\n\n"
        "If you received a packaged build, reinstall from the full "
        "distribution folder — it should include a runtime/ directory "
        "with a bundled JRE.\n\n"
        "The checker itself can still be downloaded, but checks "
        "cannot run without Java.": (
            "Java est introuvable.\n\n"
            "Si vous exécutez depuis les sources, installez un environnement "
            "d’exécution Java (JRE 17 ou plus recommandé) et assurez-vous que "
            "java est dans le PATH.\n\n"
            "Si vous utilisez une version empaquetée, réinstallez depuis le "
            "dossier complet de distribution — il doit contenir un répertoire "
            "runtime/ avec un JRE inclus.\n\n"
            "Le vérificateur peut toujours être téléchargé, mais les "
            "contrôles ne peuvent pas s’exécuter sans Java."
        ),
        "Busy": "Occupé",
        "A check is already running. Wait for it to finish, then drop again.": (
            "Une vérification est déjà en cours. Attendez la fin, puis déposez à nouveau."
        ),
        "Unsupported drop": "Dépôt non pris en charge",
        "Drop a packaged .ebrl file or an exploded publication folder.": (
            "Déposez un fichier .ebrl empaqueté ou un dossier de publication décompressé."
        ),
        "Using first publication ({name}); ignored {count} other item(s).": (
            "Utilisation de la première publication ({name}) ; "
            "{count} autre(s) élément(s) ignoré(s)."
        ),
        "Multiple items": "Plusieurs éléments",
        "Select an eBraille publication": (
            "Sélectionner une publication eBraille"
        ),
        "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
        "All files (*.*)|*.*": (
            "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
            "Tous les fichiers (*.*)|*.*"
        ),
        "Select an exploded eBraille publication folder": (
            "Sélectionner un dossier de publication eBraille décompressé"
        ),
        "Nothing to check": "Rien à vérifier",
        "Select a publication file or folder first.": (
            "Sélectionnez d’abord un fichier ou un dossier de publication."
        ),
        "Invalid path": "Chemin invalide",
        "Path not found:\n{path}": "Chemin introuvable :\n{path}",
        "Nothing to copy": "Rien à copier",
        "Run a check first.": "Lancez d’abord une vérification.",
        "Summary copied to clipboard.": "Résumé copié dans le presse-papiers.",
        "Nothing to save": "Rien à enregistrer",
        "Save report": "Enregistrer le rapport",
        "Text files (*.txt)|*.txt|All files (*.*)|*.*": (
            "Fichiers texte (*.txt)|*.txt|Tous les fichiers (*.*)|*.*"
        ),
        "Report saved to {path}": "Rapport enregistré dans {path}",
        "Checking for updates…": "Recherche de mises à jour…",
        "Update check failed": "Échec de la recherche de mises à jour",
        "Could not check for updates:\n{error}\n\nReleases: {url}": (
            "Impossible de rechercher les mises à jour :\n{error}\n\n"
            "Versions : {url}"
        ),
        "Up to date": "À jour",
        "You have the latest checker{version}.": (
            "Vous avez la dernière version du vérificateur{version}."
        ),
        "Update available": "Mise à jour disponible",
        "A new eBraille Checker release is available.\n\n"
        "Installed: {installed}\n"
        "Latest: {tag} — {name}\n\n"
        "Download and install it now?": (
            "Une nouvelle version d’eBraille Checker est disponible.\n\n"
            "Installée : {installed}\n"
            "Dernière : {tag} — {name}\n\n"
            "Télécharger et installer maintenant ?"
        ),
        "none": "aucune",
        "Fetching latest release…": "Récupération de la dernière version…",
        "Installing {tag}…": "Installation de {tag}…",
        "Installed": "Installé",
        "Checker installed successfully.\n\n{path}": (
            "Vérificateur installé avec succès.\n\n{path}"
        ),
        "Install failed": "Échec de l’installation",
        "Installation failed:\n{error}": "Échec de l’installation :\n{error}",
        "An accessible, cross-platform front-end for the DAISY "
        "eBraille Checker.": (
            "Une interface accessible et multiplateforme pour le "
            "vérificateur eBraille de la DAISY."
        ),
        "About eBraille Checker GUI": "À propos d’eBraille Checker GUI",
        "Version {version}": "Version {version}",
        "Links": "Liens",
        "DAISY Consortium website": "Site web du consortium DAISY",
        "eBraille on the DAISY website": "eBraille sur le site DAISY",
        "eBraille specification": "Spécification eBraille",
        "eBraille Checker": "eBraille Checker",
        "Passed": "Réussi",
        "Passed with warnings": "Réussi avec avertissements",
        "Failed": "Échoué",
        "Could not complete check": "Vérification impossible",
        "Check finished. {headline}.": "Vérification terminée. {headline}.",
        "{n} fatal": "{n} fatale",
        "{n} fatals": "{n} fatales",
        "{n} error": "{n} erreur",
        "{n} errors": "{n} erreurs",
        "{n} warning": "{n} avertissement",
        "{n} warnings": "{n} avertissements",
        "{label} — no errors or warnings": "{label} — aucune erreur ni avertissement",
        "{label} — see the full log for details": (
            "{label} — voir le journal complet pour les détails"
        ),
        "{label} — {details}": "{label} — {details}",
        "no errors or warnings": "aucune erreur ni avertissement",
        "see the full log for details": "voir le journal complet pour les détails",
        "Check result: {text}": "Résultat : {text}",
        "--- Full log ---": "--- Journal complet ---",
        "Fatal": "Fatale",
        "Error": "Erreur",
        "Warning": "Avertissement",
        "Info": "Info",
        "Usage": "Usage",
        "Unknown": "Inconnu",
        "Checker {version}": "Vérificateur {version}",
        "Checker {version} (bundled)": "Vérificateur {version} (inclus)",
        "Checker installed": "Vérificateur installé",
        "Checker not installed": "Vérificateur non installé",
        "Java not found": "Java introuvable",
        "Language changed to {language}.": "Langue changée : {language}.",
    },
    LANG_ES: {
        "eBraille Checker": "eBraille Checker",
        "eBraille Checker GUI": "eBraille Checker GUI",
        "Publication": "Publicación",
        "Path:": "Ruta:",
        "Select or drop a .ebrl file or folder — checking starts automatically": (
            "Seleccione o suelte un archivo .ebrl o una carpeta — "
            "la comprobación empieza automáticamente"
        ),
        "Select &file…": "Seleccionar &archivo…",
        "Select file": "Seleccionar archivo",
        "Select a packaged publication (Ctrl+O)": (
            "Seleccionar una publicación empaquetada (Ctrl+O)"
        ),
        "Select f&older…": "Seleccionar &carpeta…",
        "Select folder": "Seleccionar carpeta",
        "Select an exploded publication folder (Ctrl+Shift+O)": (
            "Seleccionar una carpeta de publicación descomprimida (Ctrl+Shift+O)"
        ),
        "Result": "Resultado",
        "Check result": "Resultado de la comprobación",
        "No check run yet.": "Aún no se ha ejecutado ninguna comprobación.",
        "Checking…": "Comprobando…",
        "Issues": "Problemas",
        "Filter:": "Filtro:",
        "Issue filter": "Filtro de problemas",
        "All issues": "Todos los problemas",
        "Errors only": "Solo errores",
        "Warnings only": "Solo advertencias",
        "Info / usage": "Info / uso",
        "&Copy summary": "&Copiar resumen",
        "Copy the result summary (Ctrl+Shift+C)": (
            "Copiar el resumen del resultado (Ctrl+Shift+C)"
        ),
        "&Save report…": "&Guardar informe…",
        "Save the report to a file (Ctrl+S)": (
            "Guardar el informe en un archivo (Ctrl+S)"
        ),
        "Issues list": "Lista de problemas",
        "Severity": "Gravedad",
        "Code": "Código",
        "Location": "Ubicación",
        "Message": "Mensaje",
        "Show full &log": "Mostrar registro &completo",
        "Hide full &log": "Ocultar registro &completo",
        "Show or hide full log": "Mostrar u ocultar el registro completo",
        "Show or hide the full checker log (Ctrl+L)": (
            "Mostrar u ocultar el registro completo (Ctrl+L)"
        ),
        "Full checker log": "Registro completo",
        "&File": "&Archivo",
        "Select &file…\tCtrl+O": "Seleccionar &archivo…\tCtrl+O",
        "Select f&older…\tCtrl+Shift+O": "Seleccionar &carpeta…\tCtrl+Shift+O",
        "&Save report…\tCtrl+S": "&Guardar informe…\tCtrl+S",
        "E&xit\tEsc": "&Salir\tEsc",
        "&Edit": "&Editar",
        "&Copy summary\tCtrl+Shift+C": "&Copiar resumen\tCtrl+Shift+C",
        "C&lear results\tCtrl+Shift+N": "&Borrar resultados\tCtrl+Shift+N",
        "A check is already running. Wait for it to finish, then clear.": (
            "Ya hay una comprobación en curso. Espere a que termine y luego borre."
        ),
        "&Tools": "&Herramientas",
        "&Re-check publication\tF5": "&Volver a comprobar\tF5",
        "Show/hide full &log\tCtrl+L": "Mostrar/ocultar registro &completo\tCtrl+L",
        "Check for &updates…": "Buscar &actualizaciones…",
        "&Download / reinstall checker…": "&Descargar / reinstalar el comprobador…",
        "&Language": "&Idioma",
        "&Help": "A&yuda",
        "&About": "&Acerca de",
        "Starting…": "Iniciando…",
        "Ready": "Listo",
        "Java required": "Se requiere Java",
        "Java was not found.\n\n"
        "If you are running from source, install a Java Runtime "
        "(JRE 17 or newer recommended) and ensure java is on your PATH.\n\n"
        "If you received a packaged build, reinstall from the full "
        "distribution folder — it should include a runtime/ directory "
        "with a bundled JRE.\n\n"
        "The checker itself can still be downloaded, but checks "
        "cannot run without Java.": (
            "No se encontró Java.\n\n"
            "Si ejecuta desde el código fuente, instale un entorno de "
            "ejecución Java (se recomienda JRE 17 o posterior) y asegúrese "
            "de que java esté en el PATH.\n\n"
            "Si recibió una versión empaquetada, reinstale desde la carpeta "
            "completa de distribución — debe incluir un directorio runtime/ "
            "con un JRE incluido.\n\n"
            "El comprobador aún se puede descargar, pero las comprobaciones "
            "no pueden ejecutarse sin Java."
        ),
        "Busy": "Ocupado",
        "A check is already running. Wait for it to finish, then drop again.": (
            "Ya hay una comprobación en curso. Espere a que termine y vuelva a soltar."
        ),
        "Unsupported drop": "Soltar no admitido",
        "Drop a packaged .ebrl file or an exploded publication folder.": (
            "Suelte un archivo .ebrl empaquetado o una carpeta de publicación descomprimida."
        ),
        "Using first publication ({name}); ignored {count} other item(s).": (
            "Usando la primera publicación ({name}); "
            "se ignoraron {count} elemento(s)."
        ),
        "Multiple items": "Varios elementos",
        "Select an eBraille publication": (
            "Seleccionar una publicación eBraille"
        ),
        "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
        "All files (*.*)|*.*": (
            "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
            "Todos los archivos (*.*)|*.*"
        ),
        "Select an exploded eBraille publication folder": (
            "Seleccionar una carpeta de publicación eBraille descomprimida"
        ),
        "Nothing to check": "Nada que comprobar",
        "Select a publication file or folder first.": (
            "Seleccione primero un archivo o carpeta de publicación."
        ),
        "Invalid path": "Ruta no válida",
        "Path not found:\n{path}": "Ruta no encontrada:\n{path}",
        "Nothing to copy": "Nada que copiar",
        "Run a check first.": "Ejecute primero una comprobación.",
        "Summary copied to clipboard.": "Resumen copiado al portapapeles.",
        "Nothing to save": "Nada que guardar",
        "Save report": "Guardar informe",
        "Text files (*.txt)|*.txt|All files (*.*)|*.*": (
            "Archivos de texto (*.txt)|*.txt|Todos los archivos (*.*)|*.*"
        ),
        "Report saved to {path}": "Informe guardado en {path}",
        "Checking for updates…": "Buscando actualizaciones…",
        "Update check failed": "Error al buscar actualizaciones",
        "Could not check for updates:\n{error}\n\nReleases: {url}": (
            "No se pudieron buscar actualizaciones:\n{error}\n\n"
            "Versiones: {url}"
        ),
        "Up to date": "Actualizado",
        "You have the latest checker{version}.": (
            "Tiene la última versión del comprobador{version}."
        ),
        "Update available": "Actualización disponible",
        "A new eBraille Checker release is available.\n\n"
        "Installed: {installed}\n"
        "Latest: {tag} — {name}\n\n"
        "Download and install it now?": (
            "Hay una nueva versión de eBraille Checker disponible.\n\n"
            "Instalada: {installed}\n"
            "Última: {tag} — {name}\n\n"
            "¿Descargar e instalar ahora?"
        ),
        "none": "ninguna",
        "Fetching latest release…": "Obteniendo la última versión…",
        "Installing {tag}…": "Instalando {tag}…",
        "Installed": "Instalado",
        "Checker installed successfully.\n\n{path}": (
            "Comprobador instalado correctamente.\n\n{path}"
        ),
        "Install failed": "Error de instalación",
        "Installation failed:\n{error}": "Error de instalación:\n{error}",
        "An accessible, cross-platform front-end for the DAISY "
        "eBraille Checker.": (
            "Una interfaz accesible y multiplataforma para el "
            "comprobador eBraille de DAISY."
        ),
        "About eBraille Checker GUI": "Acerca de eBraille Checker GUI",
        "Version {version}": "Versión {version}",
        "Links": "Enlaces",
        "DAISY Consortium website": "Sitio web del consorcio DAISY",
        "eBraille on the DAISY website": "eBraille en el sitio de DAISY",
        "eBraille specification": "Especificación eBraille",
        "eBraille Checker": "eBraille Checker",
        "Passed": "Correcto",
        "Passed with warnings": "Correcto con advertencias",
        "Failed": "Incorrecto",
        "Could not complete check": "No se pudo completar la comprobación",
        "Check finished. {headline}.": "Comprobación finalizada. {headline}.",
        "{n} fatal": "{n} fatal",
        "{n} fatals": "{n} fatales",
        "{n} error": "{n} error",
        "{n} errors": "{n} errores",
        "{n} warning": "{n} advertencia",
        "{n} warnings": "{n} advertencias",
        "{label} — no errors or warnings": "{label} — sin errores ni advertencias",
        "{label} — see the full log for details": (
            "{label} — consulte el registro completo para más detalles"
        ),
        "{label} — {details}": "{label} — {details}",
        "no errors or warnings": "sin errores ni advertencias",
        "see the full log for details": (
            "consulte el registro completo para más detalles"
        ),
        "Check result: {text}": "Resultado: {text}",
        "--- Full log ---": "--- Registro completo ---",
        "Fatal": "Fatal",
        "Error": "Error",
        "Warning": "Advertencia",
        "Info": "Info",
        "Usage": "Uso",
        "Unknown": "Desconocido",
        "Checker {version}": "Comprobador {version}",
        "Checker {version} (bundled)": "Comprobador {version} (incluido)",
        "Checker installed": "Comprobador instalado",
        "Checker not installed": "Comprobador no instalado",
        "Java not found": "Java no encontrado",
        "Language changed to {language}.": "Idioma cambiado a {language}.",
    },
    LANG_DE: {
        "eBraille Checker": "eBraille Checker",
        "eBraille Checker GUI": "eBraille Checker GUI",
        "Publication": "Publikation",
        "Path:": "Pfad:",
        "Select or drop a .ebrl file or folder — checking starts automatically": (
            "Wählen oder ziehen Sie eine .ebrl-Datei oder einen Ordner — "
            "die Prüfung startet automatisch"
        ),
        "Select &file…": "&Datei auswählen…",
        "Select file": "Datei auswählen",
        "Select a packaged publication (Ctrl+O)": (
            "Gepackte Publikation auswählen (Ctrl+O)"
        ),
        "Select f&older…": "&Ordner auswählen…",
        "Select folder": "Ordner auswählen",
        "Select an exploded publication folder (Ctrl+Shift+O)": (
            "Entpackten Publikationsordner auswählen (Ctrl+Shift+O)"
        ),
        "Result": "Ergebnis",
        "Check result": "Prüfergebnis",
        "No check run yet.": "Noch keine Prüfung ausgeführt.",
        "Checking…": "Prüfung läuft…",
        "Issues": "Probleme",
        "Filter:": "Filter:",
        "Issue filter": "Problemfilter",
        "All issues": "Alle Probleme",
        "Errors only": "Nur Fehler",
        "Warnings only": "Nur Warnungen",
        "Info / usage": "Info / Verwendung",
        "&Copy summary": "Zusammenfassung &kopieren",
        "Copy the result summary (Ctrl+Shift+C)": (
            "Ergebniszusammenfassung kopieren (Ctrl+Shift+C)"
        ),
        "&Save report…": "Bericht &speichern…",
        "Save the report to a file (Ctrl+S)": (
            "Bericht in einer Datei speichern (Ctrl+S)"
        ),
        "Issues list": "Problemliste",
        "Severity": "Schweregrad",
        "Code": "Code",
        "Location": "Ort",
        "Message": "Meldung",
        "Show full &log": "Vollständiges &Protokoll anzeigen",
        "Hide full &log": "Vollständiges &Protokoll ausblenden",
        "Show or hide full log": "Vollständiges Protokoll anzeigen oder ausblenden",
        "Show or hide the full checker log (Ctrl+L)": (
            "Vollständiges Prüferprotokoll anzeigen oder ausblenden (Ctrl+L)"
        ),
        "Full checker log": "Vollständiges Prüferprotokoll",
        "&File": "&Datei",
        "Select &file…\tCtrl+O": "&Datei auswählen…\tCtrl+O",
        "Select f&older…\tCtrl+Shift+O": "&Ordner auswählen…\tCtrl+Shift+O",
        "&Save report…\tCtrl+S": "Bericht &speichern…\tCtrl+S",
        "E&xit\tEsc": "Be&enden\tEsc",
        "&Edit": "B&earbeiten",
        "&Copy summary\tCtrl+Shift+C": "Zusammenfassung &kopieren\tCtrl+Shift+C",
        "C&lear results\tCtrl+Shift+N": "Ergebnisse &löschen\tCtrl+Shift+N",
        "A check is already running. Wait for it to finish, then clear.": (
            "Eine Prüfung läuft bereits. Warten Sie, bis sie beendet ist, "
            "und löschen Sie dann."
        ),
        "&Tools": "&Extras",
        "&Re-check publication\tF5": "Publikation erneut &prüfen\tF5",
        "Show/hide full &log\tCtrl+L": "Vollständiges &Protokoll ein-/ausblenden\tCtrl+L",
        "Check for &updates…": "Nach &Updates suchen…",
        "&Download / reinstall checker…": "Prüfer &herunterladen / neu installieren…",
        "&Language": "&Sprache",
        "&Help": "&Hilfe",
        "&About": "&Info",
        "Starting…": "Startet…",
        "Ready": "Bereit",
        "Java required": "Java erforderlich",
        "Java was not found.\n\n"
        "If you are running from source, install a Java Runtime "
        "(JRE 17 or newer recommended) and ensure java is on your PATH.\n\n"
        "If you received a packaged build, reinstall from the full "
        "distribution folder — it should include a runtime/ directory "
        "with a bundled JRE.\n\n"
        "The checker itself can still be downloaded, but checks "
        "cannot run without Java.": (
            "Java wurde nicht gefunden.\n\n"
            "Wenn Sie aus dem Quellcode starten, installieren Sie eine "
            "Java-Laufzeitumgebung (JRE 17 oder neuer empfohlen) und stellen "
            "Sie sicher, dass java im PATH liegt.\n\n"
            "Wenn Sie eine gepackte Version erhalten haben, installieren Sie "
            "sie erneut aus dem vollständigen Verteilungsordner — er sollte "
            "ein runtime/-Verzeichnis mit gebündeltem JRE enthalten.\n\n"
            "Der Prüfer kann weiterhin heruntergeladen werden, aber Prüfungen "
            "sind ohne Java nicht möglich."
        ),
        "Busy": "Beschäftigt",
        "A check is already running. Wait for it to finish, then drop again.": (
            "Eine Prüfung läuft bereits. Warten Sie auf das Ende und ziehen Sie erneut."
        ),
        "Unsupported drop": "Ablegen nicht unterstützt",
        "Drop a packaged .ebrl file or an exploded publication folder.": (
            "Legen Sie eine gepackte .ebrl-Datei oder einen entpackten "
            "Publikationsordner ab."
        ),
        "Using first publication ({name}); ignored {count} other item(s).": (
            "Erste Publikation wird verwendet ({name}); "
            "{count} weitere(s) Element(e) ignoriert."
        ),
        "Multiple items": "Mehrere Elemente",
        "Select an eBraille publication": (
            "eBraille-Publikation auswählen"
        ),
        "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
        "All files (*.*)|*.*": (
            "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
            "Alle Dateien (*.*)|*.*"
        ),
        "Select an exploded eBraille publication folder": (
            "Entpackten eBraille-Publikationsordner auswählen"
        ),
        "Nothing to check": "Nichts zu prüfen",
        "Select a publication file or folder first.": (
            "Wählen Sie zuerst eine Publikationsdatei oder einen Ordner."
        ),
        "Invalid path": "Ungültiger Pfad",
        "Path not found:\n{path}": "Pfad nicht gefunden:\n{path}",
        "Nothing to copy": "Nichts zu kopieren",
        "Run a check first.": "Führen Sie zuerst eine Prüfung aus.",
        "Summary copied to clipboard.": "Zusammenfassung in die Zwischenablage kopiert.",
        "Nothing to save": "Nichts zu speichern",
        "Save report": "Bericht speichern",
        "Text files (*.txt)|*.txt|All files (*.*)|*.*": (
            "Textdateien (*.txt)|*.txt|Alle Dateien (*.*)|*.*"
        ),
        "Report saved to {path}": "Bericht gespeichert unter {path}",
        "Checking for updates…": "Suche nach Updates…",
        "Update check failed": "Update-Prüfung fehlgeschlagen",
        "Could not check for updates:\n{error}\n\nReleases: {url}": (
            "Updates konnten nicht geprüft werden:\n{error}\n\n"
            "Versionen: {url}"
        ),
        "Up to date": "Aktuell",
        "You have the latest checker{version}.": (
            "Sie haben den neuesten Prüfer{version}."
        ),
        "Update available": "Update verfügbar",
        "A new eBraille Checker release is available.\n\n"
        "Installed: {installed}\n"
        "Latest: {tag} — {name}\n\n"
        "Download and install it now?": (
            "Eine neue eBraille-Checker-Version ist verfügbar.\n\n"
            "Installiert: {installed}\n"
            "Neueste: {tag} — {name}\n\n"
            "Jetzt herunterladen und installieren?"
        ),
        "none": "keine",
        "Fetching latest release…": "Neueste Version wird geladen…",
        "Installing {tag}…": "{tag} wird installiert…",
        "Installed": "Installiert",
        "Checker installed successfully.\n\n{path}": (
            "Prüfer erfolgreich installiert.\n\n{path}"
        ),
        "Install failed": "Installation fehlgeschlagen",
        "Installation failed:\n{error}": "Installation fehlgeschlagen:\n{error}",
        "An accessible, cross-platform front-end for the DAISY "
        "eBraille Checker.": (
            "Eine barrierefreie, plattformübergreifende Oberfläche für den "
            "DAISY eBraille Checker."
        ),
        "About eBraille Checker GUI": "Info zu eBraille Checker GUI",
        "Version {version}": "Version {version}",
        "Links": "Links",
        "DAISY Consortium website": "Website des DAISY-Konsortiums",
        "eBraille on the DAISY website": "eBraille auf der DAISY-Website",
        "eBraille specification": "eBraille-Spezifikation",
        "eBraille Checker": "eBraille Checker",
        "Passed": "Bestanden",
        "Passed with warnings": "Bestanden mit Warnungen",
        "Failed": "Fehlgeschlagen",
        "Could not complete check": "Prüfung konnte nicht abgeschlossen werden",
        "Check finished. {headline}.": "Prüfung beendet. {headline}.",
        "{n} fatal": "{n} fataler Fehler",
        "{n} fatals": "{n} fatale Fehler",
        "{n} error": "{n} Fehler",
        "{n} errors": "{n} Fehler",
        "{n} warning": "{n} Warnung",
        "{n} warnings": "{n} Warnungen",
        "{label} — no errors or warnings": "{label} — keine Fehler oder Warnungen",
        "{label} — see the full log for details": (
            "{label} — Details im vollständigen Protokoll"
        ),
        "{label} — {details}": "{label} — {details}",
        "no errors or warnings": "keine Fehler oder Warnungen",
        "see the full log for details": "Details im vollständigen Protokoll",
        "Check result: {text}": "Ergebnis: {text}",
        "--- Full log ---": "--- Vollständiges Protokoll ---",
        "Fatal": "Fatal",
        "Error": "Fehler",
        "Warning": "Warnung",
        "Info": "Info",
        "Usage": "Verwendung",
        "Unknown": "Unbekannt",
        "Checker {version}": "Prüfer {version}",
        "Checker {version} (bundled)": "Prüfer {version} (mitgeliefert)",
        "Checker installed": "Prüfer installiert",
        "Checker not installed": "Prüfer nicht installiert",
        "Java not found": "Java nicht gefunden",
        "Language changed to {language}.": "Sprache geändert: {language}.",
    },
    LANG_PT: {
        "eBraille Checker": "eBraille Checker",
        "eBraille Checker GUI": "eBraille Checker GUI",
        "Publication": "Publicação",
        "Path:": "Caminho:",
        "Select or drop a .ebrl file or folder — checking starts automatically": (
            "Selecione ou solte um ficheiro .ebrl ou uma pasta — "
            "a verificação inicia automaticamente"
        ),
        "Select &file…": "Selecionar &ficheiro…",
        "Select file": "Selecionar ficheiro",
        "Select a packaged publication (Ctrl+O)": (
            "Selecionar uma publicação empacotada (Ctrl+O)"
        ),
        "Select f&older…": "Selecionar &pasta…",
        "Select folder": "Selecionar pasta",
        "Select an exploded publication folder (Ctrl+Shift+O)": (
            "Selecionar uma pasta de publicação descompactada (Ctrl+Shift+O)"
        ),
        "Result": "Resultado",
        "Check result": "Resultado da verificação",
        "No check run yet.": "Ainda não foi executada nenhuma verificação.",
        "Checking…": "A verificar…",
        "Issues": "Problemas",
        "Filter:": "Filtro:",
        "Issue filter": "Filtro de problemas",
        "All issues": "Todos os problemas",
        "Errors only": "Apenas erros",
        "Warnings only": "Apenas avisos",
        "Info / usage": "Info / utilização",
        "&Copy summary": "&Copiar resumo",
        "Copy the result summary (Ctrl+Shift+C)": (
            "Copiar o resumo do resultado (Ctrl+Shift+C)"
        ),
        "&Save report…": "&Guardar relatório…",
        "Save the report to a file (Ctrl+S)": (
            "Guardar o relatório num ficheiro (Ctrl+S)"
        ),
        "Issues list": "Lista de problemas",
        "Severity": "Gravidade",
        "Code": "Código",
        "Location": "Localização",
        "Message": "Mensagem",
        "Show full &log": "Mostrar registo &completo",
        "Hide full &log": "Ocultar registo &completo",
        "Show or hide full log": "Mostrar ou ocultar o registo completo",
        "Show or hide the full checker log (Ctrl+L)": (
            "Mostrar ou ocultar o registo completo (Ctrl+L)"
        ),
        "Full checker log": "Registo completo",
        "&File": "&Ficheiro",
        "Select &file…\tCtrl+O": "Selecionar &ficheiro…\tCtrl+O",
        "Select f&older…\tCtrl+Shift+O": "Selecionar &pasta…\tCtrl+Shift+O",
        "&Save report…\tCtrl+S": "&Guardar relatório…\tCtrl+S",
        "E&xit\tEsc": "&Sair\tEsc",
        "&Edit": "&Editar",
        "&Copy summary\tCtrl+Shift+C": "&Copiar resumo\tCtrl+Shift+C",
        "C&lear results\tCtrl+Shift+N": "&Limpar resultados\tCtrl+Shift+N",
        "A check is already running. Wait for it to finish, then clear.": (
            "Já está a decorrer uma verificação. Aguarde que termine e depois limpe."
        ),
        "&Tools": "&Ferramentas",
        "&Re-check publication\tF5": "&Verificar novamente\tF5",
        "Show/hide full &log\tCtrl+L": "Mostrar/ocultar registo &completo\tCtrl+L",
        "Check for &updates…": "Procurar &atualizações…",
        "&Download / reinstall checker…": "&Descarregar / reinstalar o verificador…",
        "&Language": "&Idioma",
        "&Help": "A&juda",
        "&About": "&Acerca de",
        "Starting…": "A iniciar…",
        "Ready": "Pronto",
        "Java required": "Java necessário",
        "Java was not found.\n\n"
        "If you are running from source, install a Java Runtime "
        "(JRE 17 or newer recommended) and ensure java is on your PATH.\n\n"
        "If you received a packaged build, reinstall from the full "
        "distribution folder — it should include a runtime/ directory "
        "with a bundled JRE.\n\n"
        "The checker itself can still be downloaded, but checks "
        "cannot run without Java.": (
            "O Java não foi encontrado.\n\n"
            "Se estiver a executar a partir do código-fonte, instale um "
            "ambiente de execução Java (recomenda-se JRE 17 ou mais recente) "
            "e certifique-se de que o java está no PATH.\n\n"
            "Se recebeu uma versão empacotada, reinstale a partir da pasta "
            "completa de distribuição — deve incluir um diretório runtime/ "
            "com um JRE incluído.\n\n"
            "O verificador ainda pode ser descarregado, mas as verificações "
            "não podem ser executadas sem Java."
        ),
        "Busy": "Ocupado",
        "A check is already running. Wait for it to finish, then drop again.": (
            "Já existe uma verificação em curso. Aguarde que termine e solte novamente."
        ),
        "Unsupported drop": "Soltar não suportado",
        "Drop a packaged .ebrl file or an exploded publication folder.": (
            "Solte um ficheiro .ebrl empacotado ou uma pasta de publicação descompactada."
        ),
        "Using first publication ({name}); ignored {count} other item(s).": (
            "A utilizar a primeira publicação ({name}); "
            "ignorado(s) {count} outro(s) item(ns)."
        ),
        "Multiple items": "Vários itens",
        "Select an eBraille publication": (
            "Selecionar uma publicação eBraille"
        ),
        "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
        "All files (*.*)|*.*": (
            "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
            "Todos os ficheiros (*.*)|*.*"
        ),
        "Select an exploded eBraille publication folder": (
            "Selecionar uma pasta de publicação eBraille descompactada"
        ),
        "Nothing to check": "Nada a verificar",
        "Select a publication file or folder first.": (
            "Selecione primeiro um ficheiro ou pasta de publicação."
        ),
        "Invalid path": "Caminho inválido",
        "Path not found:\n{path}": "Caminho não encontrado:\n{path}",
        "Nothing to copy": "Nada a copiar",
        "Run a check first.": "Execute primeiro uma verificação.",
        "Summary copied to clipboard.": "Resumo copiado para a área de transferência.",
        "Nothing to save": "Nada a guardar",
        "Save report": "Guardar relatório",
        "Text files (*.txt)|*.txt|All files (*.*)|*.*": (
            "Ficheiros de texto (*.txt)|*.txt|Todos os ficheiros (*.*)|*.*"
        ),
        "Report saved to {path}": "Relatório guardado em {path}",
        "Checking for updates…": "A procurar atualizações…",
        "Update check failed": "Falha ao procurar atualizações",
        "Could not check for updates:\n{error}\n\nReleases: {url}": (
            "Não foi possível procurar atualizações:\n{error}\n\n"
            "Versões: {url}"
        ),
        "Up to date": "Atualizado",
        "You have the latest checker{version}.": (
            "Tem a versão mais recente do verificador{version}."
        ),
        "Update available": "Atualização disponível",
        "A new eBraille Checker release is available.\n\n"
        "Installed: {installed}\n"
        "Latest: {tag} — {name}\n\n"
        "Download and install it now?": (
            "Está disponível uma nova versão do eBraille Checker.\n\n"
            "Instalada: {installed}\n"
            "Mais recente: {tag} — {name}\n\n"
            "Descarregar e instalar agora?"
        ),
        "none": "nenhuma",
        "Fetching latest release…": "A obter a versão mais recente…",
        "Installing {tag}…": "A instalar {tag}…",
        "Installed": "Instalado",
        "Checker installed successfully.\n\n{path}": (
            "Verificador instalado com sucesso.\n\n{path}"
        ),
        "Install failed": "Falha na instalação",
        "Installation failed:\n{error}": "Falha na instalação:\n{error}",
        "An accessible, cross-platform front-end for the DAISY "
        "eBraille Checker.": (
            "Uma interface acessível e multiplataforma para o "
            "verificador eBraille da DAISY."
        ),
        "About eBraille Checker GUI": "Acerca do eBraille Checker GUI",
        "Version {version}": "Versão {version}",
        "Links": "Ligações",
        "DAISY Consortium website": "Site do consórcio DAISY",
        "eBraille on the DAISY website": "eBraille no site da DAISY",
        "eBraille specification": "Especificação eBraille",
        "eBraille Checker": "eBraille Checker",
        "Passed": "Aprovado",
        "Passed with warnings": "Aprovado com avisos",
        "Failed": "Reprovado",
        "Could not complete check": "Não foi possível concluir a verificação",
        "Check finished. {headline}.": "Verificação concluída. {headline}.",
        "{n} fatal": "{n} fatal",
        "{n} fatals": "{n} fatais",
        "{n} error": "{n} erro",
        "{n} errors": "{n} erros",
        "{n} warning": "{n} aviso",
        "{n} warnings": "{n} avisos",
        "{label} — no errors or warnings": "{label} — sem erros nem avisos",
        "{label} — see the full log for details": (
            "{label} — consulte o registo completo para mais detalhes"
        ),
        "{label} — {details}": "{label} — {details}",
        "no errors or warnings": "sem erros nem avisos",
        "see the full log for details": (
            "consulte o registo completo para mais detalhes"
        ),
        "Check result: {text}": "Resultado: {text}",
        "--- Full log ---": "--- Registo completo ---",
        "Fatal": "Fatal",
        "Error": "Erro",
        "Warning": "Aviso",
        "Info": "Info",
        "Usage": "Utilização",
        "Unknown": "Desconhecido",
        "Checker {version}": "Verificador {version}",
        "Checker {version} (bundled)": "Verificador {version} (incluído)",
        "Checker installed": "Verificador instalado",
        "Checker not installed": "Verificador não instalado",
        "Java not found": "Java não encontrado",
        "Language changed to {language}.": "Idioma alterado para {language}.",
    },
}

_current_language = DEFAULT_LANGUAGE


def detect_os_language() -> str:
    """Map the OS UI / locale language to a supported app language."""
    import locale
    import os
    import sys

    candidates: list[str] = []

    if sys.platform == "win32":
        try:
            import ctypes

            # Primary language IDs: https://learn.microsoft.com/windows/win32/intl/language-identifier-constants-and-strings
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = lang_id & 0x3FF
            win_map = {
                0x09: LANG_EN,  # English
                0x0C: LANG_FR,  # French
                0x0A: LANG_ES,  # Spanish
                0x07: LANG_DE,  # German
                0x16: LANG_PT,  # Portuguese
            }
            if primary in win_map:
                return win_map[primary]
        except (AttributeError, OSError, ValueError):
            pass

    if sys.platform == "darwin":
        try:
            # AppleLanguages preference, e.g. ("fr-FR", "en-GB", …)
            import subprocess

            out = subprocess.run(
                ["defaults", "read", "-g", "AppleLanguages"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout:
                for token in out.stdout.replace("(", " ").replace(")", " ").replace(
                    '"', " "
                ).replace(",", " ").split():
                    candidates.append(token.strip())
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        loc = locale.getlocale()
        if loc and loc[0]:
            candidates.append(loc[0])
    except (TypeError, ValueError):
        pass

    try:
        # Deprecated but still useful on some platforms
        loc = locale.getdefaultlocale()  # type: ignore[attr-defined]
        if loc and loc[0]:
            candidates.append(loc[0])
    except (AttributeError, TypeError, ValueError):
        pass

    for key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(key)
        if value:
            # LANGUAGE can be a colon-separated list
            for part in value.replace(";", ":").split(":"):
                part = part.strip()
                if part:
                    candidates.append(part.split(".")[0])

    for raw in candidates:
        code = raw.replace("_", "-").lower()
        if code.startswith("fr"):
            return LANG_FR
        if code.startswith("es"):
            return LANG_ES
        if code.startswith("de"):
            return LANG_DE
        if code.startswith("pt"):
            return LANG_PT
        if code.startswith("en"):
            return LANG_EN

    return DEFAULT_LANGUAGE


def load_language() -> str:
    """Load saved language, or detect from the OS UI language on first run."""
    global _current_language
    data = read_settings()
    lang = str(data.get("language", ""))
    if lang in LANGUAGES:
        _current_language = lang
        return lang
    detected = detect_os_language()
    _current_language = detected
    return detected


def save_language(lang: str) -> None:
    global _current_language
    if lang not in LANGUAGES:
        lang = DEFAULT_LANGUAGE
    _current_language = lang
    update_settings(language=lang)


def get_language() -> str:
    return _current_language


def set_language(lang: str) -> None:
    save_language(lang)


def _(message: str, **kwargs: object) -> str:
    """Translate message; optional format kwargs applied after lookup."""
    catalog = _TRANSLATIONS.get(_current_language, {})
    text = catalog.get(message, message)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


def ngettext(singular: str, plural: str, n: int) -> str:
    key = singular if n == 1 else plural
    return _(key, n=n)
