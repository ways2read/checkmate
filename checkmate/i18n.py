"""UI language support for CheckMate."""

from __future__ import annotations

from .settings import read_settings, update_settings

# BCP 47-ish codes used in settings and menus
LANG_EN = "en"
LANG_FR = "fr"
LANG_ES = "es"
LANG_DE = "de"
LANG_PT = "pt"
LANG_DA = "da"
LANG_NL = "nl"
LANG_FI = "fi"
LANG_HI = "hi"
LANG_NB = "nb"  # Norwegian Bokmål (OS "no" locales map here)
LANG_RU = "ru"
LANG_SV = "sv"

LANGUAGES: dict[str, str] = {
    LANG_EN: "English",
    LANG_FR: "Français",
    LANG_ES: "Español",
    LANG_DE: "Deutsch",
    LANG_PT: "Português",
    LANG_DA: "Dansk",
    LANG_NL: "Nederlands",
    LANG_FI: "Suomi",
    LANG_HI: "हिन्दी",
    LANG_NB: "Norsk",
    LANG_RU: "Русский",
    LANG_SV: "Svenska",
}

# English names for AI prompts (must cover every LANGUAGES code).
LANGUAGE_DISPLAY_NAMES: dict[str, str] = {
    LANG_EN: "English",
    LANG_FR: "French",
    LANG_ES: "Spanish",
    LANG_DE: "German",
    LANG_PT: "Portuguese",
    LANG_DA: "Danish",
    LANG_NL: "Dutch",
    LANG_FI: "Finnish",
    LANG_HI: "Hindi",
    LANG_NB: "Norwegian",
    LANG_RU: "Russian",
    LANG_SV: "Swedish",
}

DEFAULT_LANGUAGE = LANG_EN

# English msgid → translation. Missing keys fall back to English.
_TRANSLATIONS: dict[str, dict[str, str]] = {
    LANG_FR: {
        "eBraille Checker": "eBraille Checker",
        "CheckMate": "CheckMate",
        "Publication": "Publication",
        "Path:": "Chemin :",
        "Select or drop a .ebrl / .epub / .pdf file or folder — "
        "checking starts automatically": (
            "Sélectionnez ou déposez un fichier .ebrl / .epub / .pdf ou un dossier — "
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
        "Source:": "Source :",
        "Issue source filter": "Filtre par vérificateur",
        "EPUBCheck + Ace": "EPUBCheck + Ace",
        "Show issues from a specific checker, or all": (
            "Afficher les problèmes d'un vérificateur précis, ou tous"
        ),
        "All issues": "Tous les problèmes",
        "Errors only": "Erreurs uniquement",
        "Warnings only": "Avertissements uniquement",
        "Info / usage": "Info / usage",
        "Show one example of each issue": (
            "Afficher un exemple de chaque problème"
        ),
        "&Copy summary": "&Copier le résumé",
        "Copy the result summary (Ctrl+Shift+C)": (
            "Copier le résumé du résultat (Ctrl+Shift+C)"
        ),
        "&Report…": "&Rapport…",
        "View or save reports, AI overview (when available), "
        "copy the summary, or view the full log": (
            "Afficher ou enregistrer les rapports, aperçu IA (si disponible), "
            "copier le résumé ou afficher le journal complet"
        ),
        "Issues list": "Liste des problèmes",
        "Severity": "Sévérité",
        "Occurrences": "Occurrences",
        "Code": "Code",
        "Location": "Emplacement",
        "Message": "Message",
        "Issue details": "Détails du problème",
        "Severity: {value}": "Sévérité : {value}",
        "Code: {value}": "Code : {value}",
        "(none)": "(aucun)",
        "Press Enter or double-click an issue to read the full details.": (
            "Appuyez sur Entrée ou double-cliquez un problème pour lire "
            "tous les détails."
        ),
        "Issues hint": "Conseil sur les problèmes",
        "Note": "Remarque",
        "Full checker log": "Journal complet",
        "The log is empty.": "Le journal est vide.",
        "&File": "&Fichier",
        "Select &file…\tCtrl+O": "Sélectionner un &fichier…\tCtrl+O",
        "Select f&older…\tCtrl+Shift+O": "Sélectionner un d&ossier…\tCtrl+Shift+O",
        "&Report": "&Rapport",
        "View &text report\tCtrl+T": "Afficher le rapport &texte\tCtrl+T",
        "Save &text report…\tCtrl+Shift+S": (
            "Enregistrer le rapport &texte…\tCtrl+Shift+S"
        ),
        "View &HTML report in browser\tCtrl+H": (
            "Afficher le rapport &HTML dans le navigateur\tCtrl+H"
        ),
        "AI &overview…\tCtrl+Shift+A": "Aperçu &IA…\tCtrl+Shift+A",
        "Save &HTML report…\tCtrl+S": "Enregistrer le rapport &HTML…\tCtrl+S",
        "Save &HTML report…": "Enregistrer le rapport &HTML…",
        "E&xit\tEsc": "&Quitter\tÉchap",
        "&Copy summary\tCtrl+Shift+C": "&Copier le résumé\tCtrl+Shift+C",
        "C&lear results\tCtrl+Shift+N": "&Effacer les résultats\tCtrl+Shift+N",
        "C&lear results": "&Effacer les résultats",
        "A check is already running. Wait for it to finish, then clear.": (
            "Une vérification est déjà en cours. Attendez qu’elle se termine, "
            "puis effacez."
        ),
        "&Tools": "&Outils",
        "&Re-check publication\tF5": "&Revérifier la publication\tF5",
        "Show &issues always": "Toujours afficher les &problèmes",
        "When checked, open the issues list automatically after a check "
        "that finds issues (instead of pressing Show issues)": (
            "Si coché, ouvre automatiquement la liste des problèmes après une "
            "vérification qui en trouve (sans appuyer sur Afficher les problèmes)"
        ),
        "View full &log\tCtrl+L": "Afficher le journal &complet\tCtrl+L",
        "Check for &updates…": "Rechercher des &mises à jour…",
        "&Download / reinstall checkers…": "&Télécharger / réinstaller les vérificateurs…",
        "&Language": "&Langue",
        "&Help": "&Aide",
        "&About": "À &propos",
        "Starting…": "Démarrage…",
        "Ready": "Prêt",
        "In the explanation: focus starts at the top; Tab moves between links. "
        "Tab after the last link, or Ctrl+Tab, moves to the next dialog control.": (
            "Dans l’explication : le focus commence en haut ; Tab parcourt les liens. "
            "Tab après le dernier lien, ou Ctrl+Tab, passe au contrôle suivant."
        ),
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
        "Drop a packaged .ebrl, .epub, or .pdf file, or an exploded "
        "eBraille/EPUB publication folder.": (
            "Déposez un fichier .ebrl, .epub ou .pdf empaqueté, ou un dossier de "
            "publication eBraille/EPUB décompressé."
        ),
        "Using first publication ({name}); ignored {count} other item(s).": (
            "Utilisation de la première publication ({name}) ; "
            "{count} autre(s) élément(s) ignoré(s)."
        ),
        "Multiple items": "Plusieurs éléments",
        "Select an eBraille, EPUB, or PDF publication": (
            "Sélectionner une publication eBraille, EPUB ou PDF"
        ),
        "Publications (*.ebrl;*.epub;*.pdf)|"
        "*.ebrl;*.Ebrl;*.EBRL;*.epub;*.EPUB;*.pdf;*.PDF|"
        "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
        "EPUB (*.epub)|*.epub;*.EPUB|"
        "PDF (*.pdf)|*.pdf;*.PDF|"
        "All files (*.*)|*.*": (
            "Publications (*.ebrl;*.epub;*.pdf)|"
            "*.ebrl;*.Ebrl;*.EBRL;*.epub;*.EPUB;*.pdf;*.PDF|"
            "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
            "EPUB (*.epub)|*.epub;*.EPUB|"
            "PDF (*.pdf)|*.pdf;*.PDF|"
            "Tous les fichiers (*.*)|*.*"
        ),
        "Select an exploded eBraille or EPUB publication folder": (
            "Sélectionner un dossier de publication eBraille ou EPUB décompressé"
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
        "Nothing to view": "Rien à afficher",
        "Save text report": "Enregistrer le rapport texte",
        "Save HTML report": "Enregistrer le rapport HTML",
        "HTML files (*.html)|*.html;*.htm|All files (*.*)|*.*": (
            "Fichiers HTML (*.html)|*.html;*.htm|Tous les fichiers (*.*)|*.*"
        ),
        "Text files (*.txt)|*.txt|All files (*.*)|*.*": (
            "Fichiers texte (*.txt)|*.txt|Tous les fichiers (*.*)|*.*"
        ),
        "Report saved to {path}": "Rapport enregistré dans {path}",
        "Opened HTML report in browser.": (
            "Rapport HTML ouvert dans le navigateur."
        ),
        "Could not open HTML report:\n{error}": (
            "Impossible d’ouvrir le rapport HTML :\n{error}"
        ),
        "Check report": "Rapport de vérification",
        "EPUBCheck report": "Rapport EPUBCheck",
        "eBraille Checker report": "Rapport eBraille Checker",
        "veraPDF report": "Rapport veraPDF",
        "Checker": "Vérificateur",
        "Date": "Date",
        "GUI version": "Version de l’interface",
        "No issues listed.": "Aucun problème listé.",
        "Generated by CheckMate": (
            "Généré par CheckMate"
        ),
        "Skip to issues": "Aller aux problèmes",
        "Issue counts": "Nombre de problèmes",
        "All sources": "Toutes les sources",
        "Filter issues": "Filtrer les problèmes",
        "Search": "Rechercher",
        "Search issues": "Rechercher des problèmes",
        "Clear filters": "Effacer les filtres",
        "Showing {visible} of {total}": "Affichage de {visible} sur {total}",
        "No matching issues.": "Aucun problème correspondant.",
        "{n} info": "{n} info",
        "{n} infos": "{n} infos",
        "{n} usage": "{n} usage",
        "{n} usages": "{n} usages",
        "Checking for updates…": "Recherche de mises à jour…",
        "Update check failed": "Échec de la recherche de mises à jour",
        "Could not check for updates:\n{error}": (
            "Impossible de rechercher les mises à jour :\n{error}"
        ),
        "Up to date": "À jour",
        "You have the latest checkers.\n\n{detail}": (
            "Vous avez les dernières versions des vérificateurs.\n\n{detail}"
        ),
        "Update available": "Mise à jour disponible",
        "New checker releases are available.\n\n"
        "{detail}\n\n"
        "Download and install them now?": (
            "De nouvelles versions des vérificateurs sont disponibles.\n\n"
            "{detail}\n\n"
            "Télécharger et installer maintenant ?"
        ),
        "{name}\n  Installed: {installed}\n  Latest: {tag} — {label}": (
            "{name}\n  Installée : {installed}\n  Dernière : {tag} — {label}"
        ),
        "Download and reinstall the latest checkers now?\n\n{detail}": (
            "Télécharger et réinstaller les dernières versions maintenant ?\n\n{detail}"
        ),
        "none": "aucune",
        "Fetching latest releases…": "Récupération des dernières versions…",
        "Installing {tag}…": "Installation de {tag}…",
        "Installed": "Installé",
        "Checkers installed successfully.\n\n{path}": (
            "Vérificateurs installés avec succès.\n\n{path}"
        ),
        "Install failed": "Échec de l’installation",
        "Installation failed:\n{error}": "Échec de l’installation :\n{error}",
        "An accessible, cross-platform front-end for the DAISY "
        "eBraille Checker, W3C EPUBCheck, and veraPDF (PDF/UA).": (
            "Une interface accessible et multiplateforme pour le "
            "vérificateur eBraille de la DAISY, EPUBCheck du W3C et "
            "veraPDF (PDF/UA)."
        ),
        "EPUBCheck": "EPUBCheck",
        "veraPDF": "veraPDF",
        "About CheckMate": "À propos de CheckMate",
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
        "{name} {version}": "{name} {version}",
        "{name} {version} (bundled)": "{name} {version} (inclus)",
        "{name} installed": "{name} installé",
        "{name} not installed": "{name} non installé",
        "Publication: {path}": "Publication : {path}",
        "Checker: {name} {version}": "Vérificateur : {name} {version}",
        "Checker: {name}": "Vérificateur : {name}",
        "Date: {when}": "Date : {when}",
        "Parser": "Analyseur",
        "Build date": "Date de compilation",
        "Processing time": "Temps de traitement",
        "Validation profile": "Profil de validation",
        "Total rules in profile": "Règles totales dans le profil",
        "Passed checks": "Contrôles réussis",
        "Failed checks": "Contrôles échoués",
        "Java not found": "Java introuvable",
        "Language changed to {language}.": "Langue changée : {language}.",
        "Explain with AI": "Expliquer avec l’IA",
        "AI assistance": "Assistance IA",
        "AI overview": "Aperçu IA",
        "Overall assessment": "Évaluation générale",
        "Main themes": "Thèmes principaux",
        "Suggested priorities": "Priorités suggérées",
        "Practical next steps": "Prochaines étapes pratiques",
        "Caveats": "Précautions",
        "Writing overview…": "Rédaction de l’aperçu…",
        "Nothing to overview": "Rien à résumer",
        "Save AI overview as HTML": "Enregistrer l’aperçu IA en HTML",
        "Save AI overview as Markdown": "Enregistrer l’aperçu IA en Markdown",
        "A check is already running. Wait for it to finish, then try again.": (
            "Une vérification est déjà en cours. Attendez qu’elle se termine, "
            "puis réessayez."
        ),
        "Opening issue details…": "Ouverture des détails du problème…",
        "Loading AI view…": "Chargement de la vue IA…",
        "Suggest fix with AI": "Suggérer une correction avec l’IA",
        "Suggest all like this…": "Tout suggérer comme ceci…",
        "Ask AI to suggest a minimal markup fix for this EPUB "
        "or eBraille issue (uses FIDO AI settings)": (
            "Demander à l’IA de suggérer une correction minimale du balisage "
            "pour ce problème EPUB ou eBraille (utilise les réglages IA de FIDO)"
        ),
        "Ask AI to suggest unique fixes for every issue with the "
        "same checker code in this report (uses FIDO AI settings)": (
            "Demander à l’IA de suggérer des corrections uniques pour chaque "
            "problème portant le même code dans ce rapport (utilise les réglages IA de FIDO)"
        ),
        "Apply fix and validate": "Appliquer la correction et valider",
        "Write the proposed fix into the publication "
        "(creates a .bak backup first)": (
            "Écrire la correction proposée dans la publication "
            "(crée d’abord une sauvegarde .bak)"
        ),
        "Write the proposed fix into the publication, "
        "then re-check and confirm whether the issue is resolved": (
            "Écrire la correction proposée dans la publication, "
            "puis revérifier et confirmer si le problème est résolu"
        ),
        "Write the proposed fix into the publication, "
        "then re-check automatically": (
            "Écrire la correction proposée dans la publication, "
            "puis revérifier automatiquement"
        ),
        "Proposed fix": "Correction proposée",
        "Before": "Avant",
        "After": "Après",
        "File": "Fichier",
        "(no rationale)": "(aucune justification)",
        "Suggesting fix…": "Suggestion de correction…",
        "Suggesting fixes…": "Suggestion des corrections…",
        "Applying fix…": "Application de la correction…",
        "Could not propose a fix.": "Impossible de proposer une correction.",
        "Could not apply the fix.": "Impossible d’appliquer la correction.",
        "Fix suggested. Review, then Apply fix and validate.": (
            "Correction suggérée. Vérifiez, puis appliquez et validez la correction."
        ),
        "Batch fix suggested ({n} patch(es)). Review, then "
        "Apply fix and validate.": (
            "Correction groupée suggérée ({n} correctif(s)). Vérifiez, puis "
            "appliquez et validez la correction."
        ),
        "This proposal covers {n} text replacement(s) for {m} matching issue(s).": (
            "Cette proposition couvre {n} remplacement(s) de texte pour "
            "{m} problème(s) correspondant(s)."
        ),
        "Patch": "Correctif",
        "Skipped": "Ignorés",
        "All matching issues with code {code} appear to be resolved "
        "({before} → {after}).": (
            "Tous les problèmes correspondant au code {code} semblent résolus "
            "({before} → {after})."
        ),
        "Matching issues with code {code}: {before} before, "
        "{after} after the batch fix.": (
            "Problèmes correspondant au code {code} : {before} avant, "
            "{after} après la correction groupée."
        ),
        "Patches applied: {n}.": "Correctifs appliqués : {n}.",
        "Fix applied": "Correction appliquée",
        "Fix applied. Re-check the publication with F5.": (
            "Correction appliquée. Revérifiez la publication avec F5."
        ),
        "Fix applied. Backup: {path}. Re-check with F5.": (
            "Correction appliquée. Sauvegarde : {path}. Revérifiez avec F5."
        ),
        "The issue appears to be resolved after applying the fix.": (
            "Le problème semble résolu après application de la correction."
        ),
        "The targeted issue appears to be resolved (code: {code}).": (
            "Le problème ciblé semble résolu (code : {code})."
        ),
        "The targeted issue is still reported after the fix was applied "
        "(code: {code}).": (
            "Le problème ciblé est toujours signalé après application de la "
            "correction (code : {code})."
        ),
        "Totals before: {fatals} fatal(s), {errors} error(s), "
        "{warnings} warning(s).": (
            "Totaux avant : {fatals} fatal(s), {errors} erreur(s), "
            "{warnings} avertissement(s)."
        ),
        "Totals after: {fatals} fatal(s), {errors} error(s), "
        "{warnings} warning(s).": (
            "Totaux après : {fatals} fatal(s), {errors} erreur(s), "
            "{warnings} avertissement(s)."
        ),
        "Overall errors/warnings decreased.": (
            "Le total des erreurs/avertissements a diminué."
        ),
        "Overall errors/warnings did not decrease after the fix.": (
            "Le total des erreurs/avertissements n’a pas diminué après "
            "la correction."
        ),
        "Fixing this Ace issue introduced {n} new EPUBCheck "
        "error(s) that were not present before:": (
            "La correction de ce problème Ace a introduit {n} nouvelle(s) "
            "erreur(s) EPUBCheck absentes auparavant :"
        ),
        "No new EPUBCheck errors were introduced by this Ace fix.": (
            "Aucune nouvelle erreur EPUBCheck n’a été introduite par "
            "cette correction Ace."
        ),
        "…and {n} more.": "…et {n} de plus.",
        "No backup file was found to revert.": (
            "Aucun fichier de sauvegarde n’a été trouvé pour le rétablissement."
        ),
        "Do you want to revert to the backup?\n\n"
        "Backup:\n{backup}": (
            "Voulez-vous rétablir la sauvegarde ?\n\n"
            "Sauvegarde :\n{backup}"
        ),
        "Fix confirmed": "Correction confirmée",
        "Edit log:\n{path}": "Journal des modifications :\n{path}",
        "Edit changelog": "Journal des modifications",
        "View edit &changelog…\tCtrl+Shift+G": (
            "Voir le &journal des modifications…\tCtrl+Shift+G"
        ),
        "Open in &browser": "Ouvrir dans le &navigateur",
        "Open &folder": "Ouvrir le &dossier",
        "No changelog": "Aucun journal",
        "No CheckMate edit changelog was found for this publication.\n\n"
        "A changelog is created beside the file (or inside an exploded "
        "folder) when you apply an AI fix.": (
            "Aucun journal des modifications CheckMate n’a été trouvé pour "
            "cette publication.\n\n"
            "Un journal est créé à côté du fichier (ou dans un dossier "
            "éclaté) lorsque vous appliquez une correction par IA."
        ),
        "Could not read the changelog:\n{error}": (
            "Impossible de lire le journal des modifications :\n{error}"
        ),
        "Could not open the changelog in a browser:\n{error}": (
            "Impossible d’ouvrir le journal dans un navigateur :\n{error}"
        ),
        "Could not open the folder:\n{error}": (
            "Impossible d’ouvrir le dossier :\n{error}"
        ),
        "Open the CheckMate edit changelog for this publication "
        "(AI fixes and backups), when one exists": (
            "Ouvrir le journal des modifications CheckMate pour cette "
            "publication (corrections IA et sauvegardes), s’il existe"
        ),
        "Open a formatted HTML view in your browser": (
            "Ouvrir une vue HTML formatée dans votre navigateur"
        ),
        "Reveal the changelog file in the file manager": (
            "Afficher le fichier journal dans le gestionnaire de fichiers"
        ),
        "Fix not confirmed": "Correction non confirmée",
        "Re-check failed": "Échec de la revérification",
        "Reverted": "Rétabli",
        "The publication was reverted to the backup.": (
            "La publication a été rétablie à partir de la sauvegarde."
        ),
        "Could not revert to the backup:\n{detail}": (
            "Impossible de rétablir à partir de la sauvegarde :\n{detail}"
        ),
        "Do you want to revert to the backup created before the fix?": (
            "Voulez-vous rétablir la sauvegarde créée avant la correction ?"
        ),
        "The publication was changed, but the re-check could not be completed.\n\n"
        "{detail}": (
            "La publication a été modifiée, mais la revérification n’a pas pu "
            "être terminée.\n\n{detail}"
        ),
        "The issue is still reported after the fix was applied "
        "(code: {code}).\n\n"
        "No backup file was found to revert.": (
            "Le problème est toujours signalé après application de la "
            "correction (code : {code}).\n\n"
            "Aucun fichier de sauvegarde n’a été trouvé pour le rétablissement."
        ),
        "The issue is still reported after the fix was applied "
        "(code: {code}).\n\n"
        "Do you want to revert to the backup?\n\n"
        "Backup:\n{backup}": (
            "Le problème est toujours signalé après application de la "
            "correction (code : {code}).\n\n"
            "Voulez-vous rétablir la sauvegarde ?\n\n"
            "Sauvegarde :\n{backup}"
        ),
        "Apply this fix to the publication?\n\n"
        "File: {file}\n\n"
        "A .bak backup will be created first. "
        "Re-check the publication (F5) afterward to verify.": (
            "Appliquer cette correction à la publication ?\n\n"
            "Fichier : {file}\n\n"
            "Une sauvegarde .bak sera créée d’abord. "
            "Revérifiez ensuite la publication (F5)."
        ),
        "Fix with AI is only available for EPUB and eBraille publications.": (
            "Corriger avec l’IA n’est disponible que pour les publications "
            "EPUB et eBraille."
        ),
        "The AI did not return an applicable patch. You can still read the "
        "reply above, or try Explain with AI.": (
            "L’IA n’a pas renvoyé de correctif applicable. Réessayez "
            "Corriger avec l’IA, ou utilisez Expliquer avec l’IA."
        ),
        "The AI did not return an applicable patch. Try Fix with AI again, "
        "or use Explain with AI.": (
            "L’IA n’a pas renvoyé de correctif applicable. Réessayez "
            "Corriger avec l’IA, ou utilisez Expliquer avec l’IA."
        ),
        "The AI reply was incomplete or unusable (draft text or invalid JSON). "
        "Try Fix with AI again.": (
            "La réponse de l’IA était incomplète ou inutilisable (brouillon "
            "ou JSON invalide). Réessayez Corriger avec l’IA."
        ),
        "The AI reply was cut off before a complete patch was ready. "
        "Try Fix with AI again.": (
            "La réponse de l’IA a été coupée avant qu’un correctif complet "
            "soit prêt. Réessayez Corriger avec l’IA."
        ),
        "The AI proposed a patch that does not match the publication file. "
        "Try Fix with AI again.": (
            "L’IA a proposé un correctif qui ne correspond pas au fichier "
            "de la publication. Réessayez Corriger avec l’IA."
        ),
        "The proposed patch has an empty original string.": (
            "Le correctif proposé a une chaîne d’origine vide."
        ),
        "Could not apply the fix: the original text was not found in the file "
        "(it may have changed).": (
            "Impossible d’appliquer la correction : le texte d’origine "
            "est introuvable dans le fichier (il a peut‑être changé)."
        ),
        "Could not apply the fix: the original text appears more than once "
        "in the file.": (
            "Impossible d’appliquer la correction : le texte d’origine "
            "apparaît plusieurs fois dans le fichier."
        ),
        "The publication path is missing or no longer exists.": (
            "Le chemin de la publication est manquant ou n’existe plus."
        ),
        "Could not find the file to edit inside the publication.": (
            "Impossible de trouver le fichier à modifier dans la publication."
        ),
        "This publication type cannot be edited in place by CheckMate.": (
            "Ce type de publication ne peut pas être modifié sur place "
            "par CheckMate."
        ),
        "Could not write the fixed publication.": (
            "Impossible d’écrire la publication corrigée."
        ),
        "The publication package could not be read or rebuilt.": (
            "Le paquet de publication n’a pas pu être lu ou reconstruit."
        ),
        "What this means": "Ce que cela signifie",
        "Why it matters": "Pourquoi c’est important",
        "Where in the file": "Où dans le fichier",
        "How to fix": "Comment corriger",
        "Learn more": "En savoir plus",
        "Model:": "Modèle :",
        "AI model": "Modèle IA",
        "AI model selected in FIDO (read-only)": (
            "Modèle IA sélectionné dans FIDO (lecture seule)"
        ),
        "(no model selected)": "(aucun modèle sélectionné)",
        "View in browser": "Afficher dans le navigateur",
        "Open the explanation in your web browser": (
            "Ouvrir l’explication dans le navigateur web"
        ),
        "Save as HTML…": "Enregistrer en HTML…",
        "Save the explanation as an HTML file": (
            "Enregistrer l’explication dans un fichier HTML"
        ),
        "Save as Markdown…": "Enregistrer en Markdown…",
        "Save the explanation as a Markdown file": (
            "Enregistrer l’explication dans un fichier Markdown"
        ),
        "Copy to clipboard": "Copier dans le presse-papiers",
        "Copy the explanation markdown to the clipboard": (
            "Copier l’explication Markdown dans le presse-papiers"
        ),
        "Save AI explanation as HTML": "Enregistrer l’explication IA en HTML",
        "Save AI explanation as Markdown": (
            "Enregistrer l’explication IA en Markdown"
        ),
        "Markdown files (*.md)|*.md;*.markdown|All files (*.*)|*.*": (
            "Fichiers Markdown (*.md)|*.md;*.markdown|Tous les fichiers (*.*)|*.*"
        ),
        "Opened in browser.": "Ouvert dans le navigateur.",
        "Saved to {path}": "Enregistré dans {path}",
        "Copied to clipboard.": "Copié dans le presse-papiers.",
        "Copied to clipboard": "Copié dans le presse-papiers",
        "The explanation was copied to the clipboard.": (
            "L’explication a été copiée dans le presse-papiers."
        ),
        "AI status": "État IA",
        "Could not copy to the clipboard.": (
            "Impossible de copier dans le presse-papiers."
        ),
        "Close": "Fermer",
        "Could not open the explanation in a browser:\n{error}": (
            "Impossible d’ouvrir l’explication dans un navigateur :\n{error}"
        ),
        "Could not save the explanation:\n{error}": (
            "Impossible d’enregistrer l’explication :\n{error}"
        ),
        "Ask AI to explain this issue in plain language "
        "(uses FIDO AI settings)": (
            "Demander à l’IA d’expliquer ce problème en langage clair "
            "(utilise les réglages IA de FIDO)"
        ),
        "AI explanation": "Explication IA",
        "Follow-up question": "Question de suivi",
        "Ask a follow-up question…": "Posez une question de suivi…",
        "Ask": "Demander",
        "Explaining…": "Explication…",
        "Thinking…": "Réflexion…",
        "Done": "Terminé",
        "This explanation was generated by AI and may contain mistakes!": (
            "Cette explication a été générée par l’IA et peut contenir des erreurs !"
        ),
        "Follow-up": "Suivi",
        "You asked": "Vous avez demandé",
        "Could not explain this issue.": "Impossible d’expliquer ce problème.",
        "AI support is not available (litellm is not installed).": (
            "L’assistance IA n’est pas disponible (litellm n’est pas installé)."
        ),
        "No AI credentials found. Configure API keys or an unlock code in FIDO.": (
            "Aucun identifiant IA trouvé. Configurez des clés API ou un code "
            "de déverrouillage dans FIDO."
        ),
        "No API key is available for the selected AI model. Check FIDO settings or your unlock code.": (
            "Aucune clé API disponible pour le modèle IA sélectionné. "
            "Vérifiez les réglages FIDO ou votre code de déverrouillage."
        ),
        "No AI model is selected in FIDO settings.": (
            "Aucun modèle IA n’est sélectionné dans les réglages FIDO."
        ),
        "The AI services unlock code was not found. Check the code in FIDO.": (
            "Le code de déverrouillage des services IA est introuvable. "
            "Vérifiez le code dans FIDO."
        ),
        "Could not reach the unlock server or AI provider. Check your connection.": (
            "Impossible de joindre le serveur de déverrouillage ou le "
            "fournisseur d’IA. Vérifiez votre connexion."
        ),
        "The unlock server returned invalid data.": (
            "Le serveur de déverrouillage a renvoyé des données invalides."
        ),
        "The AI services unlock code has expired.": (
            "Le code de déverrouillage des services IA a expiré."
        ),
        "The unlock data could not be processed.": (
            "Les données de déverrouillage n’ont pas pu être traitées."
        ),
        "Could not refresh AI credentials from the unlock code.": (
            "Impossible d’actualiser les identifiants IA à partir du "
            "code de déverrouillage."
        ),
        "The unlock code did not provide usable API keys.": (
            "Le code de déverrouillage n’a pas fourni de clés API utilisables."
        ),
        "The AI returned an empty response.": (
            "L’IA a renvoyé une réponse vide."
        ),
        "Enter a follow-up question.": "Saisissez une question de suivi.",
        "The AI provider returned an error.": (
            "Le fournisseur d’IA a renvoyé une erreur."
        ),
        "Explain the issue first, then ask a follow-up.": (
            "Expliquez d’abord le problème, puis posez une question de suivi."
        ),
        "The AI request timed out. Try again, or check your connection and FIDO settings.": (
            "La requête IA a expiré. Réessayez, ou vérifiez votre connexion "
            "et les paramètres FIDO."
        ),
        "The AI request was cancelled.": "La requête IA a été annulée.",
        "Checking AI credentials…": "Vérification des identifiants IA…",
        "Checking AI connection…": "Vérification de la connexion IA…",
        "Loading AI libraries…": "Chargement des bibliothèques IA…",
        "Could not load AI libraries.": "Impossible de charger les bibliothèques IA.",
        "Cancelling…": "Annulation…",
        "Cancelled.": "Annulé.",
        "Open debugging &log…": "Ouvrir le journal de &débogage…",
        "No debugging log has been written yet.": (
            "Aucun journal de débogage n’a encore été écrit."
        ),
        "Debugging log": "Journal de débogage",
        "Could not open the debugging log:\n{path}": (
            "Impossible d’ouvrir le journal de débogage :\n{path}"
        ),
        "Continuing truncated reply…": "Poursuite de la réponse tronquée…",
        "\n\n---\n*Note: The AI reply was cut off again. "
        "Ask a follow-up such as “Please continue.”*": (
            "\n\n---\n*Remarque : la réponse de l’IA a encore été coupée. "
            "Posez une question de suivi telle que « Veuillez continuer. »*"
        ),
    },
    LANG_ES: {
        "eBraille Checker": "eBraille Checker",
        "CheckMate": "CheckMate",
        "Publication": "Publicación",
        "Path:": "Ruta:",
        "Select or drop a .ebrl / .epub / .pdf file or folder — "
        "checking starts automatically": (
            "Seleccione o suelte un archivo .ebrl / .epub / .pdf o una carpeta — "
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
        "Source:": "Origen:",
        "Issue source filter": "Filtro por verificador",
        "EPUBCheck + Ace": "EPUBCheck + Ace",
        "Show issues from a specific checker, or all": (
            "Mostrar problemas de un verificador concreto, o de todos"
        ),
        "All issues": "Todos los problemas",
        "Errors only": "Solo errores",
        "Warnings only": "Solo advertencias",
        "Info / usage": "Info / uso",
        "Show one example of each issue": (
            "Mostrar un ejemplo de cada problema"
        ),
        "&Copy summary": "&Copiar resumen",
        "Copy the result summary (Ctrl+Shift+C)": (
            "Copiar el resumen del resultado (Ctrl+Shift+C)"
        ),
        "&Report…": "&Informe…",
        "View or save reports, AI overview (when available), "
        "copy the summary, or view the full log": (
            "Ver o guardar informes, resumen de IA (si está disponible), "
            "copiar el resumen o ver el registro completo"
        ),
        "Issues list": "Lista de problemas",
        "Severity": "Gravedad",
        "Occurrences": "Ocurrencias",
        "Code": "Código",
        "Location": "Ubicación",
        "Message": "Mensaje",
        "Issue details": "Detalles del problema",
        "Severity: {value}": "Gravedad: {value}",
        "Code: {value}": "Código: {value}",
        "(none)": "(ninguno)",
        "Press Enter or double-click an issue to read the full details.": (
            "Pulse Intro o haga doble clic en un problema para leer "
            "todos los detalles."
        ),
        "Issues hint": "Consejo de problemas",
        "Note": "Nota",
        "Full checker log": "Registro completo",
        "The log is empty.": "El registro está vacío.",
        "&File": "&Archivo",
        "Select &file…\tCtrl+O": "Seleccionar &archivo…\tCtrl+O",
        "Select f&older…\tCtrl+Shift+O": "Seleccionar &carpeta…\tCtrl+Shift+O",
        "&Report": "&Informe",
        "View &text report\tCtrl+T": "Ver informe de &texto\tCtrl+T",
        "Save &text report…\tCtrl+Shift+S": (
            "Guardar informe de &texto…\tCtrl+Shift+S"
        ),
        "View &HTML report in browser\tCtrl+H": (
            "Ver informe &HTML en el navegador\tCtrl+H"
        ),
        "AI &overview…\tCtrl+Shift+A": "Resumen de &IA…\tCtrl+Shift+A",
        "Save &HTML report…\tCtrl+S": "Guardar informe &HTML…\tCtrl+S",
        "Save &HTML report…": "Guardar informe &HTML…",
        "E&xit\tEsc": "&Salir\tEsc",
        "&Copy summary\tCtrl+Shift+C": "&Copiar resumen\tCtrl+Shift+C",
        "C&lear results\tCtrl+Shift+N": "&Borrar resultados\tCtrl+Shift+N",
        "C&lear results": "&Borrar resultados",
        "A check is already running. Wait for it to finish, then clear.": (
            "Ya hay una comprobación en curso. Espere a que termine y luego borre."
        ),
        "&Tools": "&Herramientas",
        "&Re-check publication\tF5": "&Volver a comprobar\tF5",
        "Show &issues always": "Mostrar &problemas siempre",
        "When checked, open the issues list automatically after a check "
        "that finds issues (instead of pressing Show issues)": (
            "Si está marcado, abre automáticamente la lista de problemas después "
            "de una comprobación que encuentre problemas (sin pulsar Mostrar problemas)"
        ),
        "View full &log\tCtrl+L": "Ver registro &completo\tCtrl+L",
        "Check for &updates…": "Buscar &actualizaciones…",
        "&Download / reinstall checkers…": "&Descargar / reinstalar los comprobadores…",
        "&Language": "&Idioma",
        "&Help": "A&yuda",
        "&About": "&Acerca de",
        "Starting…": "Iniciando…",
        "Ready": "Listo",
        "In the explanation: focus starts at the top; Tab moves between links. "
        "Tab after the last link, or Ctrl+Tab, moves to the next dialog control.": (
            "En la explicación: el foco empieza arriba; Tab se mueve entre enlaces. "
            "Tab después del último enlace, o Ctrl+Tab, pasa al siguiente control."
        ),
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
        "Drop a packaged .ebrl, .epub, or .pdf file, or an exploded "
        "eBraille/EPUB publication folder.": (
            "Suelte un archivo .ebrl, .epub o .pdf empaquetado, o una carpeta de "
            "publicación eBraille/EPUB descomprimida."
        ),
        "Using first publication ({name}); ignored {count} other item(s).": (
            "Usando la primera publicación ({name}); "
            "se ignoraron {count} elemento(s)."
        ),
        "Multiple items": "Varios elementos",
        "Select an eBraille, EPUB, or PDF publication": (
            "Seleccionar una publicación eBraille, EPUB o PDF"
        ),
        "Publications (*.ebrl;*.epub;*.pdf)|"
        "*.ebrl;*.Ebrl;*.EBRL;*.epub;*.EPUB;*.pdf;*.PDF|"
        "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
        "EPUB (*.epub)|*.epub;*.EPUB|"
        "PDF (*.pdf)|*.pdf;*.PDF|"
        "All files (*.*)|*.*": (
            "Publications (*.ebrl;*.epub;*.pdf)|"
            "*.ebrl;*.Ebrl;*.EBRL;*.epub;*.EPUB;*.pdf;*.PDF|"
            "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
            "EPUB (*.epub)|*.epub;*.EPUB|"
            "PDF (*.pdf)|*.pdf;*.PDF|"
            "Todos los archivos (*.*)|*.*"
        ),
        "Select an exploded eBraille or EPUB publication folder": (
            "Seleccionar una carpeta de publicación eBraille o EPUB descomprimida"
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
        "Nothing to view": "Nada que ver",
        "Save text report": "Guardar informe de texto",
        "Save HTML report": "Guardar informe HTML",
        "HTML files (*.html)|*.html;*.htm|All files (*.*)|*.*": (
            "Archivos HTML (*.html)|*.html;*.htm|Todos los archivos (*.*)|*.*"
        ),
        "Text files (*.txt)|*.txt|All files (*.*)|*.*": (
            "Archivos de texto (*.txt)|*.txt|Todos los archivos (*.*)|*.*"
        ),
        "Report saved to {path}": "Informe guardado en {path}",
        "Opened HTML report in browser.": (
            "Informe HTML abierto en el navegador."
        ),
        "Could not open HTML report:\n{error}": (
            "No se pudo abrir el informe HTML:\n{error}"
        ),
        "Check report": "Informe de comprobación",
        "EPUBCheck report": "Informe EPUBCheck",
        "eBraille Checker report": "Informe eBraille Checker",
        "veraPDF report": "Informe veraPDF",
        "Checker": "Comprobador",
        "Date": "Fecha",
        "GUI version": "Versión de la interfaz",
        "No issues listed.": "No hay problemas listados.",
        "Generated by CheckMate": (
            "Generado por CheckMate"
        ),
        "Skip to issues": "Ir a los problemas",
        "Issue counts": "Recuento de problemas",
        "All sources": "Todas las fuentes",
        "Filter issues": "Filtrar problemas",
        "Search": "Buscar",
        "Search issues": "Buscar problemas",
        "Clear filters": "Borrar filtros",
        "Showing {visible} of {total}": "Mostrando {visible} de {total}",
        "No matching issues.": "No hay problemas coincidentes.",
        "{n} info": "{n} info",
        "{n} infos": "{n} infos",
        "{n} usage": "{n} uso",
        "{n} usages": "{n} usos",
        "Checking for updates…": "Buscando actualizaciones…",
        "Update check failed": "Error al buscar actualizaciones",
        "Could not check for updates:\n{error}": (
            "No se pudieron buscar actualizaciones:\n{error}"
        ),
        "Up to date": "Actualizado",
        "You have the latest checkers.\n\n{detail}": (
            "Tiene las últimas versiones de los comprobadores.\n\n{detail}"
        ),
        "Update available": "Actualización disponible",
        "New checker releases are available.\n\n"
        "{detail}\n\n"
        "Download and install them now?": (
            "Hay nuevas versiones de los comprobadores disponibles.\n\n"
            "{detail}\n\n"
            "¿Descargar e instalar ahora?"
        ),
        "{name}\n  Installed: {installed}\n  Latest: {tag} — {label}": (
            "{name}\n  Instalada: {installed}\n  Última: {tag} — {label}"
        ),
        "Download and reinstall the latest checkers now?\n\n{detail}": (
            "¿Descargar y reinstalar las últimas versiones ahora?\n\n{detail}"
        ),
        "none": "ninguna",
        "Fetching latest releases…": "Obteniendo las últimas versiones…",
        "Installing {tag}…": "Instalando {tag}…",
        "Installed": "Instalado",
        "Checkers installed successfully.\n\n{path}": (
            "Comprobadores instalados correctamente.\n\n{path}"
        ),
        "Install failed": "Error de instalación",
        "Installation failed:\n{error}": "Error de instalación:\n{error}",
        "An accessible, cross-platform front-end for the DAISY "
        "eBraille Checker, W3C EPUBCheck, and veraPDF (PDF/UA).": (
            "Una interfaz accesible y multiplataforma para el "
            "comprobador eBraille de DAISY, EPUBCheck del W3C y "
            "veraPDF (PDF/UA)."
        ),
        "EPUBCheck": "EPUBCheck",
        "veraPDF": "veraPDF",
        "About CheckMate": "Acerca de CheckMate",
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
        "{name} {version}": "{name} {version}",
        "{name} {version} (bundled)": "{name} {version} (incluido)",
        "{name} installed": "{name} instalado",
        "{name} not installed": "{name} no instalado",
        "Publication: {path}": "Publicación: {path}",
        "Checker: {name} {version}": "Comprobador: {name} {version}",
        "Checker: {name}": "Comprobador: {name}",
        "Date: {when}": "Fecha: {when}",
        "Parser": "Analizador",
        "Build date": "Fecha de compilación",
        "Processing time": "Tiempo de procesamiento",
        "Validation profile": "Perfil de validación",
        "Total rules in profile": "Reglas totales en el perfil",
        "Passed checks": "Comprobaciones correctas",
        "Failed checks": "Comprobaciones fallidas",
        "Java not found": "Java no encontrado",
        "Language changed to {language}.": "Idioma cambiado a {language}.",
        "Explain with AI": "Explicar con IA",
        "AI assistance": "Asistencia de IA",
        "AI overview": "Resumen de IA",
        "Overall assessment": "Evaluación general",
        "Main themes": "Temas principales",
        "Suggested priorities": "Prioridades sugeridas",
        "Practical next steps": "Próximos pasos prácticos",
        "Caveats": "Advertencias",
        "Writing overview…": "Redactando el resumen…",
        "Nothing to overview": "Nada que resumir",
        "Save AI overview as HTML": "Guardar resumen de IA como HTML",
        "Save AI overview as Markdown": "Guardar resumen de IA como Markdown",
        "A check is already running. Wait for it to finish, then try again.": (
            "Ya hay una comprobación en curso. Espere a que termine e "
            "inténtelo de nuevo."
        ),
        "Opening issue details…": "Abriendo detalles del problema…",
        "Loading AI view…": "Cargando la vista de IA…",
        "Suggest fix with AI": "Sugerir corrección con IA",
        "Suggest all like this…": "Sugerir todos así…",
        "Ask AI to suggest a minimal markup fix for this EPUB "
        "or eBraille issue (uses FIDO AI settings)": (
            "Pedir a la IA una corrección mínima de marcado para este "
            "problema de EPUB o eBraille (usa la configuración de IA de FIDO)"
        ),
        "Ask AI to suggest unique fixes for every issue with the "
        "same checker code in this report (uses FIDO AI settings)": (
            "Pedir a la IA correcciones únicas para cada problema con el "
            "mismo código en este informe (usa la configuración de IA de FIDO)"
        ),
        "Apply fix and validate": "Aplicar corrección y validar",
        "Write the proposed fix into the publication "
        "(creates a .bak backup first)": (
            "Escribir la corrección propuesta en la publicación "
            "(crea primero una copia .bak)"
        ),
        "Write the proposed fix into the publication, "
        "then re-check and confirm whether the issue is resolved": (
            "Escribir la corrección propuesta en la publicación "
            "y volver a comprobar para confirmar si el problema está resuelto"
        ),
        "Write the proposed fix into the publication, "
        "then re-check automatically": (
            "Escribir la corrección propuesta en la publicación "
            "y volver a comprobar automáticamente"
        ),
        "Proposed fix": "Corrección propuesta",
        "Before": "Antes",
        "After": "Después",
        "File": "Archivo",
        "(no rationale)": "(sin justificación)",
        "Suggesting fix…": "Sugiriendo corrección…",
        "Suggesting fixes…": "Sugiriendo correcciones…",
        "Applying fix…": "Aplicando corrección…",
        "Could not propose a fix.": "No se pudo proponer una corrección.",
        "Could not apply the fix.": "No se pudo aplicar la corrección.",
        "Fix suggested. Review, then Apply fix and validate.": (
            "Corrección sugerida. Revísela y luego aplíquela y valídela."
        ),
        "Batch fix suggested ({n} patch(es)). Review, then "
        "Apply fix and validate.": (
            "Corrección por lotes sugerida ({n} parche(s)). Revísela y luego "
            "aplíquela y valídela."
        ),
        "This proposal covers {n} text replacement(s) for {m} matching issue(s).": (
            "Esta propuesta cubre {n} reemplazo(s) de texto para "
            "{m} problema(s) coincidente(s)."
        ),
        "Patch": "Parche",
        "Skipped": "Omitidos",
        "All matching issues with code {code} appear to be resolved "
        "({before} → {after}).": (
            "Todos los problemas con el código {code} parecen resueltos "
            "({before} → {after})."
        ),
        "Matching issues with code {code}: {before} before, "
        "{after} after the batch fix.": (
            "Problemas con el código {code}: {before} antes, "
            "{after} después de la corrección por lotes."
        ),
        "Patches applied: {n}.": "Parches aplicados: {n}.",
        "Fix applied": "Corrección aplicada",
        "Fix applied. Re-check the publication with F5.": (
            "Corrección aplicada. Vuelva a comprobar la publicación con F5."
        ),
        "Fix applied. Backup: {path}. Re-check with F5.": (
            "Corrección aplicada. Copia de seguridad: {path}. "
            "Vuelva a comprobar con F5."
        ),
        "The issue appears to be resolved after applying the fix.": (
            "El problema parece resuelto tras aplicar la corrección."
        ),
        "The targeted issue appears to be resolved (code: {code}).": (
            "El problema concreto parece resuelto (código: {code})."
        ),
        "The targeted issue is still reported after the fix was applied "
        "(code: {code}).": (
            "El problema concreto sigue apareciendo tras aplicar la "
            "corrección (código: {code})."
        ),
        "Totals before: {fatals} fatal(s), {errors} error(s), "
        "{warnings} warning(s).": (
            "Totales antes: {fatals} fatal(es), {errors} error(es), "
            "{warnings} advertencia(s)."
        ),
        "Totals after: {fatals} fatal(s), {errors} error(s), "
        "{warnings} warning(s).": (
            "Totales después: {fatals} fatal(es), {errors} error(es), "
            "{warnings} advertencia(s)."
        ),
        "Overall errors/warnings decreased.": (
            "El total de errores/advertencias disminuyó."
        ),
        "Overall errors/warnings did not decrease after the fix.": (
            "El total de errores/advertencias no disminuyó tras la corrección."
        ),
        "Fixing this Ace issue introduced {n} new EPUBCheck "
        "error(s) that were not present before:": (
            "Al corregir este problema de Ace se introdujeron {n} error(es) "
            "nuevos de EPUBCheck que antes no estaban:"
        ),
        "No new EPUBCheck errors were introduced by this Ace fix.": (
            "Esta corrección de Ace no introdujo errores nuevos de EPUBCheck."
        ),
        "…and {n} more.": "…y {n} más.",
        "No backup file was found to revert.": (
            "No se encontró ningún archivo de copia de seguridad para revertir."
        ),
        "Do you want to revert to the backup?\n\n"
        "Backup:\n{backup}": (
            "¿Desea revertir a la copia de seguridad?\n\n"
            "Copia de seguridad:\n{backup}"
        ),
        "Fix confirmed": "Corrección confirmada",
        "Edit log:\n{path}": "Registro de ediciones:\n{path}",
        "Edit changelog": "Registro de ediciones",
        "View edit &changelog…\tCtrl+Shift+G": (
            "Ver &registro de ediciones…\tCtrl+Shift+G"
        ),
        "Open in &browser": "Abrir en el &navegador",
        "Open &folder": "Abrir &carpeta",
        "No changelog": "Sin registro",
        "No CheckMate edit changelog was found for this publication.\n\n"
        "A changelog is created beside the file (or inside an exploded "
        "folder) when you apply an AI fix.": (
            "No se encontró un registro de ediciones de CheckMate para esta "
            "publicación.\n\n"
            "Se crea un registro junto al archivo (o dentro de una carpeta "
            "descomprimida) al aplicar una corrección con IA."
        ),
        "Could not read the changelog:\n{error}": (
            "No se pudo leer el registro de ediciones:\n{error}"
        ),
        "Could not open the changelog in a browser:\n{error}": (
            "No se pudo abrir el registro en un navegador:\n{error}"
        ),
        "Could not open the folder:\n{error}": (
            "No se pudo abrir la carpeta:\n{error}"
        ),
        "Open the CheckMate edit changelog for this publication "
        "(AI fixes and backups), when one exists": (
            "Abrir el registro de ediciones de CheckMate para esta "
            "publicación (correcciones IA y copias de seguridad), si existe"
        ),
        "Open a formatted HTML view in your browser": (
            "Abrir una vista HTML formateada en el navegador"
        ),
        "Reveal the changelog file in the file manager": (
            "Mostrar el archivo de registro en el explorador de archivos"
        ),
        "Fix not confirmed": "Corrección no confirmada",
        "Re-check failed": "Error al volver a comprobar",
        "Reverted": "Revertido",
        "The publication was reverted to the backup.": (
            "La publicación se revirtió a la copia de seguridad."
        ),
        "Could not revert to the backup:\n{detail}": (
            "No se pudo revertir a la copia de seguridad:\n{detail}"
        ),
        "Do you want to revert to the backup created before the fix?": (
            "¿Desea revertir a la copia de seguridad creada antes de la corrección?"
        ),
        "The publication was changed, but the re-check could not be completed.\n\n"
        "{detail}": (
            "La publicación se modificó, pero no se pudo completar la "
            "nueva comprobación.\n\n{detail}"
        ),
        "The issue is still reported after the fix was applied "
        "(code: {code}).\n\n"
        "No backup file was found to revert.": (
            "El problema sigue apareciendo tras aplicar la corrección "
            "(código: {code}).\n\n"
            "No se encontró ningún archivo de copia de seguridad para revertir."
        ),
        "The issue is still reported after the fix was applied "
        "(code: {code}).\n\n"
        "Do you want to revert to the backup?\n\n"
        "Backup:\n{backup}": (
            "El problema sigue apareciendo tras aplicar la corrección "
            "(código: {code}).\n\n"
            "¿Desea revertir a la copia de seguridad?\n\n"
            "Copia de seguridad:\n{backup}"
        ),
        "Apply this fix to the publication?\n\n"
        "File: {file}\n\n"
        "A .bak backup will be created first. "
        "Re-check the publication (F5) afterward to verify.": (
            "¿Aplicar esta corrección a la publicación?\n\n"
            "Archivo: {file}\n\n"
            "Se creará primero una copia .bak. "
            "Después, vuelva a comprobar la publicación (F5)."
        ),
        "Fix with AI is only available for EPUB and eBraille publications.": (
            "Corregir con IA solo está disponible para publicaciones "
            "EPUB y eBraille."
        ),
        "The AI did not return an applicable patch. You can still read the "
        "reply above, or try Explain with AI.": (
            "La IA no devolvió un parche aplicable. Vuelva a intentar "
            "Corregir con IA, o use Explicar con IA."
        ),
        "The AI did not return an applicable patch. Try Fix with AI again, "
        "or use Explain with AI.": (
            "La IA no devolvió un parche aplicable. Vuelva a intentar "
            "Corregir con IA, o use Explicar con IA."
        ),
        "The AI reply was incomplete or unusable (draft text or invalid JSON). "
        "Try Fix with AI again.": (
            "La respuesta de la IA estaba incompleta o era inutilizable "
            "(borrador o JSON no válido). Vuelva a intentar Corregir con IA."
        ),
        "The AI reply was cut off before a complete patch was ready. "
        "Try Fix with AI again.": (
            "La respuesta de la IA se cortó antes de un parche completo. "
            "Vuelva a intentar Corregir con IA."
        ),
        "The AI proposed a patch that does not match the publication file. "
        "Try Fix with AI again.": (
            "La IA propuso un parche que no coincide con el archivo de la "
            "publicación. Vuelva a intentar Corregir con IA."
        ),
        "The proposed patch has an empty original string.": (
            "El parche propuesto tiene una cadena original vacía."
        ),
        "Could not apply the fix: the original text was not found in the file "
        "(it may have changed).": (
            "No se pudo aplicar la corrección: no se encontró el texto "
            "original en el archivo (puede haber cambiado)."
        ),
        "Could not apply the fix: the original text appears more than once "
        "in the file.": (
            "No se pudo aplicar la corrección: el texto original aparece "
            "más de una vez en el archivo."
        ),
        "The publication path is missing or no longer exists.": (
            "Falta la ruta de la publicación o ya no existe."
        ),
        "Could not find the file to edit inside the publication.": (
            "No se pudo encontrar el archivo a editar dentro de la publicación."
        ),
        "This publication type cannot be edited in place by CheckMate.": (
            "CheckMate no puede editar este tipo de publicación in situ."
        ),
        "Could not write the fixed publication.": (
            "No se pudo escribir la publicación corregida."
        ),
        "The publication package could not be read or rebuilt.": (
            "No se pudo leer o reconstruir el paquete de la publicación."
        ),
        "What this means": "Qué significa",
        "Why it matters": "Por qué importa",
        "Where in the file": "Dónde en el archivo",
        "How to fix": "Cómo corregirlo",
        "Learn more": "Más información",
        "Model:": "Modelo:",
        "AI model": "Modelo de IA",
        "AI model selected in FIDO (read-only)": (
            "Modelo de IA seleccionado en FIDO (solo lectura)"
        ),
        "(no model selected)": "(ningún modelo seleccionado)",
        "View in browser": "Ver en el navegador",
        "Open the explanation in your web browser": (
            "Abrir la explicación en el navegador web"
        ),
        "Save as HTML…": "Guardar como HTML…",
        "Save the explanation as an HTML file": (
            "Guardar la explicación como archivo HTML"
        ),
        "Save as Markdown…": "Guardar como Markdown…",
        "Save the explanation as a Markdown file": (
            "Guardar la explicación como archivo Markdown"
        ),
        "Copy to clipboard": "Copiar al portapapeles",
        "Copy the explanation markdown to the clipboard": (
            "Copiar la explicación Markdown al portapapeles"
        ),
        "Save AI explanation as HTML": "Guardar explicación de IA como HTML",
        "Save AI explanation as Markdown": (
            "Guardar explicación de IA como Markdown"
        ),
        "Markdown files (*.md)|*.md;*.markdown|All files (*.*)|*.*": (
            "Archivos Markdown (*.md)|*.md;*.markdown|Todos los archivos (*.*)|*.*"
        ),
        "Opened in browser.": "Abierto en el navegador.",
        "Saved to {path}": "Guardado en {path}",
        "Copied to clipboard.": "Copiado al portapapeles.",
        "Copied to clipboard": "Copiado al portapapeles",
        "The explanation was copied to the clipboard.": (
            "La explicación se copió al portapapeles."
        ),
        "AI status": "Estado de la IA",
        "Could not copy to the clipboard.": (
            "No se pudo copiar al portapapeles."
        ),
        "Close": "Cerrar",
        "Could not open the explanation in a browser:\n{error}": (
            "No se pudo abrir la explicación en un navegador:\n{error}"
        ),
        "Could not save the explanation:\n{error}": (
            "No se pudo guardar la explicación:\n{error}"
        ),
        "Ask AI to explain this issue in plain language "
        "(uses FIDO AI settings)": (
            "Pedir a la IA que explique este problema en lenguaje sencillo "
            "(usa la configuración de IA de FIDO)"
        ),
        "AI explanation": "Explicación de la IA",
        "Follow-up question": "Pregunta de seguimiento",
        "Ask a follow-up question…": "Haga una pregunta de seguimiento…",
        "Ask": "Preguntar",
        "Explaining…": "Explicando…",
        "Thinking…": "Pensando…",
        "Done": "Listo",
        "This explanation was generated by AI and may contain mistakes!": (
            "Esta explicación fue generada por IA y puede contener errores."
        ),
        "Follow-up": "Seguimiento",
        "You asked": "Usted preguntó",
        "Could not explain this issue.": "No se pudo explicar este problema.",
        "AI support is not available (litellm is not installed).": (
            "La asistencia de IA no está disponible (litellm no está instalado)."
        ),
        "No AI credentials found. Configure API keys or an unlock code in FIDO.": (
            "No se encontraron credenciales de IA. Configure claves API o un "
            "código de desbloqueo en FIDO."
        ),
        "No API key is available for the selected AI model. Check FIDO settings or your unlock code.": (
            "No hay clave API disponible para el modelo de IA seleccionado. "
            "Revise la configuración de FIDO o su código de desbloqueo."
        ),
        "No AI model is selected in FIDO settings.": (
            "No hay ningún modelo de IA seleccionado en la configuración de FIDO."
        ),
        "The AI services unlock code was not found. Check the code in FIDO.": (
            "No se encontró el código de desbloqueo de servicios de IA. "
            "Compruebe el código en FIDO."
        ),
        "Could not reach the unlock server or AI provider. Check your connection.": (
            "No se pudo contactar con el servidor de desbloqueo o el "
            "proveedor de IA. Compruebe su conexión."
        ),
        "The unlock server returned invalid data.": (
            "El servidor de desbloqueo devolvió datos no válidos."
        ),
        "The AI services unlock code has expired.": (
            "El código de desbloqueo de servicios de IA ha caducado."
        ),
        "The unlock data could not be processed.": (
            "No se pudieron procesar los datos de desbloqueo."
        ),
        "Could not refresh AI credentials from the unlock code.": (
            "No se pudieron actualizar las credenciales de IA a partir "
            "del código de desbloqueo."
        ),
        "The unlock code did not provide usable API keys.": (
            "El código de desbloqueo no proporcionó claves API utilizables."
        ),
        "The AI returned an empty response.": (
            "La IA devolvió una respuesta vacía."
        ),
        "Enter a follow-up question.": "Introduzca una pregunta de seguimiento.",
        "The AI provider returned an error.": (
            "El proveedor de IA devolvió un error."
        ),
        "Explain the issue first, then ask a follow-up.": (
            "Explique primero el problema y luego haga una pregunta de seguimiento."
        ),
        "The AI request timed out. Try again, or check your connection and FIDO settings.": (
            "La solicitud de IA agotó el tiempo de espera. Inténtelo de nuevo, "
            "o compruebe su conexión y la configuración de FIDO."
        ),
        "The AI request was cancelled.": "La solicitud de IA se canceló.",
        "Checking AI credentials…": "Comprobando credenciales de IA…",
        "Checking AI connection…": "Comprobando conexión de IA…",
        "Loading AI libraries…": "Cargando bibliotecas de IA…",
        "Could not load AI libraries.": "No se pudieron cargar las bibliotecas de IA.",
        "Cancelling…": "Cancelando…",
        "Cancelled.": "Cancelado.",
        "Open debugging &log…": "Abrir el registro de &depuración…",
        "No debugging log has been written yet.": (
            "Todavía no se ha escrito ningún registro de depuración."
        ),
        "Debugging log": "Registro de depuración",
        "Could not open the debugging log:\n{path}": (
            "No se pudo abrir el registro de depuración:\n{path}"
        ),
        "Continuing truncated reply…": "Continuando la respuesta truncada…",
        "\n\n---\n*Note: The AI reply was cut off again. "
        "Ask a follow-up such as “Please continue.”*": (
            "\n\n---\n*Nota: la respuesta de la IA se cortó otra vez. "
            "Haga una pregunta de seguimiento como «Por favor, continúe.»*"
        ),
    },
    LANG_DE: {
        "eBraille Checker": "eBraille Checker",
        "CheckMate": "CheckMate",
        "Publication": "Publikation",
        "Path:": "Pfad:",
        "Select or drop a .ebrl / .epub / .pdf file or folder — "
        "checking starts automatically": (
            "Wählen oder ziehen Sie eine .ebrl-/.epub-/.pdf-Datei oder einen Ordner — "
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
        "Source:": "Quelle:",
        "Issue source filter": "Filter nach Prüfprogramm",
        "EPUBCheck + Ace": "EPUBCheck + Ace",
        "Show issues from a specific checker, or all": (
            "Probleme eines bestimmten Prüfprogramms oder aller anzeigen"
        ),
        "All issues": "Alle Probleme",
        "Errors only": "Nur Fehler",
        "Warnings only": "Nur Warnungen",
        "Info / usage": "Info / Verwendung",
        "Show one example of each issue": (
            "Ein Beispiel für jedes Problem anzeigen"
        ),
        "&Copy summary": "Zusammenfassung &kopieren",
        "Copy the result summary (Ctrl+Shift+C)": (
            "Ergebniszusammenfassung kopieren (Ctrl+Shift+C)"
        ),
        "&Report…": "&Bericht…",
        "View or save reports, AI overview (when available), "
        "copy the summary, or view the full log": (
            "Berichte anzeigen oder speichern, KI-Überblick (falls verfügbar), "
            "Zusammenfassung kopieren oder vollständiges Protokoll anzeigen"
        ),
        "Issues list": "Problemliste",
        "Severity": "Schweregrad",
        "Occurrences": "Vorkommen",
        "Code": "Code",
        "Location": "Ort",
        "Message": "Meldung",
        "Issue details": "Problemdetails",
        "Severity: {value}": "Schweregrad: {value}",
        "Code: {value}": "Code: {value}",
        "(none)": "(keine)",
        "Press Enter or double-click an issue to read the full details.": (
            "Drücken Sie die Eingabetaste oder doppelklicken Sie auf ein "
            "Problem, um alle Details zu lesen."
        ),
        "Issues hint": "Hinweis zu Problemen",
        "Note": "Hinweis",
        "Full checker log": "Vollständiges Prüferprotokoll",
        "The log is empty.": "Das Protokoll ist leer.",
        "&File": "&Datei",
        "Select &file…\tCtrl+O": "&Datei auswählen…\tCtrl+O",
        "Select f&older…\tCtrl+Shift+O": "&Ordner auswählen…\tCtrl+Shift+O",
        "&Report": "&Bericht",
        "View &text report\tCtrl+T": "&Textbericht anzeigen\tCtrl+T",
        "Save &text report…\tCtrl+Shift+S": (
            "&Textbericht speichern…\tCtrl+Shift+S"
        ),
        "View &HTML report in browser\tCtrl+H": (
            "&HTML-Bericht im Browser anzeigen\tCtrl+H"
        ),
        "AI &overview…\tCtrl+Shift+A": "KI-&Überblick…\tCtrl+Shift+A",
        "Save &HTML report…\tCtrl+S": "&HTML-Bericht speichern…\tCtrl+S",
        "Save &HTML report…": "&HTML-Bericht speichern…",
        "E&xit\tEsc": "Be&enden\tEsc",
        "&Copy summary\tCtrl+Shift+C": "Zusammenfassung &kopieren\tCtrl+Shift+C",
        "C&lear results\tCtrl+Shift+N": "Ergebnisse &löschen\tCtrl+Shift+N",
        "C&lear results": "Ergebnisse &löschen",
        "A check is already running. Wait for it to finish, then clear.": (
            "Eine Prüfung läuft bereits. Warten Sie, bis sie beendet ist, "
            "und löschen Sie dann."
        ),
        "&Tools": "&Extras",
        "&Re-check publication\tF5": "Publikation erneut &prüfen\tF5",
        "Show &issues always": "Probleme immer &anzeigen",
        "When checked, open the issues list automatically after a check "
        "that finds issues (instead of pressing Show issues)": (
            "Wenn aktiviert, wird die Problemliste nach einer Prüfung mit "
            "Problemen automatisch geöffnet (ohne „Probleme anzeigen“)"
        ),
        "View full &log\tCtrl+L": "Vollständiges &Protokoll anzeigen\tCtrl+L",
        "Check for &updates…": "Nach &Updates suchen…",
        "&Download / reinstall checkers…": "Prüfer &herunterladen / neu installieren…",
        "&Language": "&Sprache",
        "&Help": "&Hilfe",
        "&About": "&Info",
        "Starting…": "Startet…",
        "Ready": "Bereit",
        "In the explanation: focus starts at the top; Tab moves between links. "
        "Tab after the last link, or Ctrl+Tab, moves to the next dialog control.": (
            "In der Erklärung: der Fokus beginnt oben; Tab wechselt zwischen Links. "
            "Tab nach dem letzten Link oder Strg+Tab wechselt zum nächsten Steuerelement."
        ),
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
        "Drop a packaged .ebrl, .epub, or .pdf file, or an exploded "
        "eBraille/EPUB publication folder.": (
            "Legen Sie eine gepackte .ebrl-, .epub- oder .pdf-Datei oder einen "
            "entpackten eBraille-/EPUB-Publikationsordner ab."
        ),
        "Using first publication ({name}); ignored {count} other item(s).": (
            "Erste Publikation wird verwendet ({name}); "
            "{count} weitere(s) Element(e) ignoriert."
        ),
        "Multiple items": "Mehrere Elemente",
        "Select an eBraille, EPUB, or PDF publication": (
            "eBraille-, EPUB- oder PDF-Publikation auswählen"
        ),
        "Publications (*.ebrl;*.epub;*.pdf)|"
        "*.ebrl;*.Ebrl;*.EBRL;*.epub;*.EPUB;*.pdf;*.PDF|"
        "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
        "EPUB (*.epub)|*.epub;*.EPUB|"
        "PDF (*.pdf)|*.pdf;*.PDF|"
        "All files (*.*)|*.*": (
            "Publications (*.ebrl;*.epub;*.pdf)|"
            "*.ebrl;*.Ebrl;*.EBRL;*.epub;*.EPUB;*.pdf;*.PDF|"
            "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
            "EPUB (*.epub)|*.epub;*.EPUB|"
            "PDF (*.pdf)|*.pdf;*.PDF|"
            "Alle Dateien (*.*)|*.*"
        ),
        "Select an exploded eBraille or EPUB publication folder": (
            "Entpackten eBraille- oder EPUB-Publikationsordner auswählen"
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
        "Nothing to view": "Nichts anzuzeigen",
        "Save text report": "Textbericht speichern",
        "Save HTML report": "HTML-Bericht speichern",
        "HTML files (*.html)|*.html;*.htm|All files (*.*)|*.*": (
            "HTML-Dateien (*.html)|*.html;*.htm|Alle Dateien (*.*)|*.*"
        ),
        "Text files (*.txt)|*.txt|All files (*.*)|*.*": (
            "Textdateien (*.txt)|*.txt|Alle Dateien (*.*)|*.*"
        ),
        "Report saved to {path}": "Bericht gespeichert unter {path}",
        "Opened HTML report in browser.": (
            "HTML-Bericht im Browser geöffnet."
        ),
        "Could not open HTML report:\n{error}": (
            "HTML-Bericht konnte nicht geöffnet werden:\n{error}"
        ),
        "Check report": "Prüfbericht",
        "EPUBCheck report": "EPUBCheck-Bericht",
        "eBraille Checker report": "eBraille-Checker-Bericht",
        "veraPDF report": "veraPDF-Bericht",
        "Checker": "Prüfer",
        "Date": "Datum",
        "GUI version": "GUI-Version",
        "No issues listed.": "Keine Probleme aufgelistet.",
        "Generated by CheckMate": (
            "Erstellt mit CheckMate"
        ),
        "Skip to issues": "Zu den Problemen",
        "Issue counts": "Problemzahlen",
        "All sources": "Alle Quellen",
        "Filter issues": "Probleme filtern",
        "Search": "Suche",
        "Search issues": "Probleme suchen",
        "Clear filters": "Filter zurücksetzen",
        "Showing {visible} of {total}": "{visible} von {total} angezeigt",
        "No matching issues.": "Keine passenden Probleme.",
        "{n} info": "{n} Info",
        "{n} infos": "{n} Infos",
        "{n} usage": "{n} Verwendung",
        "{n} usages": "{n} Verwendungen",
        "Checking for updates…": "Suche nach Updates…",
        "Update check failed": "Update-Prüfung fehlgeschlagen",
        "Could not check for updates:\n{error}": (
            "Updates konnten nicht geprüft werden:\n{error}"
        ),
        "Up to date": "Aktuell",
        "You have the latest checkers.\n\n{detail}": (
            "Sie haben die neuesten Prüfer.\n\n{detail}"
        ),
        "Update available": "Update verfügbar",
        "New checker releases are available.\n\n"
        "{detail}\n\n"
        "Download and install them now?": (
            "Neue Prüferversionen sind verfügbar.\n\n"
            "{detail}\n\n"
            "Jetzt herunterladen und installieren?"
        ),
        "{name}\n  Installed: {installed}\n  Latest: {tag} — {label}": (
            "{name}\n  Installiert: {installed}\n  Neueste: {tag} — {label}"
        ),
        "Download and reinstall the latest checkers now?\n\n{detail}": (
            "Neueste Prüfer jetzt herunterladen und neu installieren?\n\n{detail}"
        ),
        "none": "keine",
        "Fetching latest releases…": "Neueste Versionen werden geladen…",
        "Installing {tag}…": "{tag} wird installiert…",
        "Installed": "Installiert",
        "Checkers installed successfully.\n\n{path}": (
            "Prüfer erfolgreich installiert.\n\n{path}"
        ),
        "Install failed": "Installation fehlgeschlagen",
        "Installation failed:\n{error}": "Installation fehlgeschlagen:\n{error}",
        "An accessible, cross-platform front-end for the DAISY "
        "eBraille Checker, W3C EPUBCheck, and veraPDF (PDF/UA).": (
            "Eine barrierefreie, plattformübergreifende Oberfläche für den "
            "DAISY eBraille Checker, W3C EPUBCheck und veraPDF (PDF/UA)."
        ),
        "EPUBCheck": "EPUBCheck",
        "veraPDF": "veraPDF",
        "About CheckMate": "Info zu CheckMate",
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
        "{name} {version}": "{name} {version}",
        "{name} {version} (bundled)": "{name} {version} (mitgeliefert)",
        "{name} installed": "{name} installiert",
        "{name} not installed": "{name} nicht installiert",
        "Publication: {path}": "Publikation: {path}",
        "Checker: {name} {version}": "Prüfer: {name} {version}",
        "Checker: {name}": "Prüfer: {name}",
        "Date: {when}": "Datum: {when}",
        "Parser": "Parser",
        "Build date": "Build-Datum",
        "Processing time": "Verarbeitungszeit",
        "Validation profile": "Validierungsprofil",
        "Total rules in profile": "Regeln im Profil gesamt",
        "Passed checks": "Bestandene Prüfungen",
        "Failed checks": "Fehlgeschlagene Prüfungen",
        "Java not found": "Java nicht gefunden",
        "Language changed to {language}.": "Sprache geändert: {language}.",
        "Explain with AI": "Mit KI erklären",
        "AI assistance": "KI-Unterstützung",
        "AI overview": "KI-Überblick",
        "Overall assessment": "Gesamteinschätzung",
        "Main themes": "Hauptthemen",
        "Suggested priorities": "Empfohlene Prioritäten",
        "Practical next steps": "Praktische nächste Schritte",
        "Caveats": "Hinweise",
        "Writing overview…": "Überblick wird erstellt…",
        "Nothing to overview": "Nichts zum Zusammenfassen",
        "Save AI overview as HTML": "KI-Überblick als HTML speichern",
        "Save AI overview as Markdown": "KI-Überblick als Markdown speichern",
        "A check is already running. Wait for it to finish, then try again.": (
            "Eine Prüfung läuft bereits. Warten Sie, bis sie fertig ist, "
            "und versuchen Sie es erneut."
        ),
        "Opening issue details…": "Problemdetails werden geöffnet…",
        "Loading AI view…": "KI-Ansicht wird geladen…",
        "Suggest fix with AI": "Korrektur mit KI vorschlagen",
        "Suggest all like this…": "Alle so vorschlagen…",
        "Ask AI to suggest a minimal markup fix for this EPUB "
        "or eBraille issue (uses FIDO AI settings)": (
            "KI um einen minimalen Markup-Fix für dieses EPUB- oder "
            "eBraille-Problem bitten (nutzt FIDO-KI-Einstellungen)"
        ),
        "Ask AI to suggest unique fixes for every issue with the "
        "same checker code in this report (uses FIDO AI settings)": (
            "KI um eindeutige Korrekturen für jedes Problem mit demselben "
            "Prüfcode in diesem Bericht bitten (nutzt FIDO-KI-Einstellungen)"
        ),
        "Apply fix and validate": "Korrektur anwenden und validieren",
        "Write the proposed fix into the publication "
        "(creates a .bak backup first)": (
            "Die vorgeschlagene Korrektur in die Publikation schreiben "
            "(erstellt zuerst eine .bak-Sicherung)"
        ),
        "Write the proposed fix into the publication, "
        "then re-check and confirm whether the issue is resolved": (
            "Die vorgeschlagene Korrektur in die Publikation schreiben, "
            "dann erneut prüfen und bestätigen, ob das Problem behoben ist"
        ),
        "Write the proposed fix into the publication, "
        "then re-check automatically": (
            "Die vorgeschlagene Korrektur in die Publikation schreiben "
            "und automatisch erneut prüfen"
        ),
        "Proposed fix": "Vorgeschlagene Korrektur",
        "Before": "Vorher",
        "After": "Nachher",
        "File": "Datei",
        "(no rationale)": "(keine Begründung)",
        "Suggesting fix…": "Korrektur wird vorgeschlagen…",
        "Suggesting fixes…": "Korrekturen werden vorgeschlagen…",
        "Applying fix…": "Korrektur wird angewendet…",
        "Could not propose a fix.": "Es konnte keine Korrektur vorgeschlagen werden.",
        "Could not apply the fix.": "Die Korrektur konnte nicht angewendet werden.",
        "Fix suggested. Review, then Apply fix and validate.": (
            "Korrektur vorgeschlagen. Prüfen, dann anwenden und validieren."
        ),
        "Batch fix suggested ({n} patch(es)). Review, then "
        "Apply fix and validate.": (
            "Sammelkorrektur vorgeschlagen ({n} Patch(es)). Prüfen, dann "
            "anwenden und validieren."
        ),
        "This proposal covers {n} text replacement(s) for {m} matching issue(s).": (
            "Dieser Vorschlag umfasst {n} Textersetzung(en) für "
            "{m} passende(s) Problem(e)."
        ),
        "Patch": "Patch",
        "Skipped": "Übersprungen",
        "All matching issues with code {code} appear to be resolved "
        "({before} → {after}).": (
            "Alle passenden Probleme mit Code {code} scheinen behoben "
            "({before} → {after})."
        ),
        "Matching issues with code {code}: {before} before, "
        "{after} after the batch fix.": (
            "Passende Probleme mit Code {code}: {before} vorher, "
            "{after} nach der Sammelkorrektur."
        ),
        "Patches applied: {n}.": "Angewendete Patches: {n}.",
        "Fix applied": "Korrektur angewendet",
        "Fix applied. Re-check the publication with F5.": (
            "Korrektur angewendet. Publikation mit F5 erneut prüfen."
        ),
        "Fix applied. Backup: {path}. Re-check with F5.": (
            "Korrektur angewendet. Sicherung: {path}. Erneut prüfen mit F5."
        ),
        "The issue appears to be resolved after applying the fix.": (
            "Das Problem scheint nach dem Anwenden der Korrektur behoben zu sein."
        ),
        "The targeted issue appears to be resolved (code: {code}).": (
            "Das gezielte Problem scheint behoben zu sein (Code: {code})."
        ),
        "The targeted issue is still reported after the fix was applied "
        "(code: {code}).": (
            "Das gezielte Problem wird nach dem Anwenden der Korrektur "
            "weiterhin gemeldet (Code: {code})."
        ),
        "Totals before: {fatals} fatal(s), {errors} error(s), "
        "{warnings} warning(s).": (
            "Summen vorher: {fatals} Fatal Error(s), {errors} Fehler, "
            "{warnings} Warnung(en)."
        ),
        "Totals after: {fatals} fatal(s), {errors} error(s), "
        "{warnings} warning(s).": (
            "Summen nachher: {fatals} Fatal Error(s), {errors} Fehler, "
            "{warnings} Warnung(en)."
        ),
        "Overall errors/warnings decreased.": (
            "Die Gesamtzahl der Fehler/Warnungen ist gesunken."
        ),
        "Overall errors/warnings did not decrease after the fix.": (
            "Die Gesamtzahl der Fehler/Warnungen ist nach der Korrektur "
            "nicht gesunken."
        ),
        "Fixing this Ace issue introduced {n} new EPUBCheck "
        "error(s) that were not present before:": (
            "Das Beheben dieses Ace-Problems hat {n} neue EPUBCheck-Fehler "
            "eingeführt, die vorher nicht vorhanden waren:"
        ),
        "No new EPUBCheck errors were introduced by this Ace fix.": (
            "Durch diese Ace-Korrektur wurden keine neuen EPUBCheck-Fehler "
            "eingeführt."
        ),
        "…and {n} more.": "…und {n} weitere.",
        "No backup file was found to revert.": (
            "Es wurde keine Sicherungsdatei zum Zurücksetzen gefunden."
        ),
        "Do you want to revert to the backup?\n\n"
        "Backup:\n{backup}": (
            "Möchten Sie die Sicherung wiederherstellen?\n\n"
            "Sicherung:\n{backup}"
        ),
        "Fix confirmed": "Korrektur bestätigt",
        "Edit log:\n{path}": "Änderungsprotokoll:\n{path}",
        "Edit changelog": "Änderungsprotokoll",
        "View edit &changelog…\tCtrl+Shift+G": (
            "Änderungs&protokoll anzeigen…\tCtrl+Shift+G"
        ),
        "Open in &browser": "Im &Browser öffnen",
        "Open &folder": "&Ordner öffnen",
        "No changelog": "Kein Protokoll",
        "No CheckMate edit changelog was found for this publication.\n\n"
        "A changelog is created beside the file (or inside an exploded "
        "folder) when you apply an AI fix.": (
            "Für diese Publikation wurde kein CheckMate-Änderungsprotokoll "
            "gefunden.\n\n"
            "Ein Protokoll wird neben der Datei (oder in einem entpackten "
            "Ordner) erstellt, wenn Sie eine KI-Korrektur anwenden."
        ),
        "Could not read the changelog:\n{error}": (
            "Änderungsprotokoll konnte nicht gelesen werden:\n{error}"
        ),
        "Could not open the changelog in a browser:\n{error}": (
            "Protokoll konnte nicht im Browser geöffnet werden:\n{error}"
        ),
        "Could not open the folder:\n{error}": (
            "Ordner konnte nicht geöffnet werden:\n{error}"
        ),
        "Open the CheckMate edit changelog for this publication "
        "(AI fixes and backups), when one exists": (
            "CheckMate-Änderungsprotokoll für diese Publikation öffnen "
            "(KI-Korrekturen und Sicherungen), falls vorhanden"
        ),
        "Open a formatted HTML view in your browser": (
            "Formatierte HTML-Ansicht im Browser öffnen"
        ),
        "Reveal the changelog file in the file manager": (
            "Protokolldatei im Dateimanager anzeigen"
        ),
        "Fix not confirmed": "Korrektur nicht bestätigt",
        "Re-check failed": "Erneute Prüfung fehlgeschlagen",
        "Reverted": "Zurückgesetzt",
        "The publication was reverted to the backup.": (
            "Die Publikation wurde auf die Sicherung zurückgesetzt."
        ),
        "Could not revert to the backup:\n{detail}": (
            "Die Sicherung konnte nicht wiederhergestellt werden:\n{detail}"
        ),
        "Do you want to revert to the backup created before the fix?": (
            "Möchten Sie die vor der Korrektur erstellte Sicherung wiederherstellen?"
        ),
        "The publication was changed, but the re-check could not be completed.\n\n"
        "{detail}": (
            "Die Publikation wurde geändert, aber die erneute Prüfung konnte "
            "nicht abgeschlossen werden.\n\n{detail}"
        ),
        "The issue is still reported after the fix was applied "
        "(code: {code}).\n\n"
        "No backup file was found to revert.": (
            "Das Problem wird nach dem Anwenden der Korrektur weiterhin "
            "gemeldet (Code: {code}).\n\n"
            "Es wurde keine Sicherungsdatei zum Zurücksetzen gefunden."
        ),
        "The issue is still reported after the fix was applied "
        "(code: {code}).\n\n"
        "Do you want to revert to the backup?\n\n"
        "Backup:\n{backup}": (
            "Das Problem wird nach dem Anwenden der Korrektur weiterhin "
            "gemeldet (Code: {code}).\n\n"
            "Möchten Sie die Sicherung wiederherstellen?\n\n"
            "Sicherung:\n{backup}"
        ),
        "Apply this fix to the publication?\n\n"
        "File: {file}\n\n"
        "A .bak backup will be created first. "
        "Re-check the publication (F5) afterward to verify.": (
            "Diese Korrektur auf die Publikation anwenden?\n\n"
            "Datei: {file}\n\n"
            "Zuerst wird eine .bak-Sicherung erstellt. "
            "Prüfen Sie die Publikation danach erneut (F5)."
        ),
        "Fix with AI is only available for EPUB and eBraille publications.": (
            "Mit KI beheben ist nur für EPUB- und eBraille-Publikationen "
            "verfügbar."
        ),
        "The AI did not return an applicable patch. You can still read the "
        "reply above, or try Explain with AI.": (
            "Die KI hat keinen anwendbaren Patch zurückgegeben. Versuchen Sie "
            "Mit KI beheben erneut, oder nutzen Sie Mit KI erklären."
        ),
        "The AI did not return an applicable patch. Try Fix with AI again, "
        "or use Explain with AI.": (
            "Die KI hat keinen anwendbaren Patch zurückgegeben. Versuchen Sie "
            "Mit KI beheben erneut, oder nutzen Sie Mit KI erklären."
        ),
        "The AI reply was incomplete or unusable (draft text or invalid JSON). "
        "Try Fix with AI again.": (
            "Die KI-Antwort war unvollständig oder unbrauchbar (Entwurf oder "
            "ungültiges JSON). Versuchen Sie Mit KI beheben erneut."
        ),
        "The AI reply was cut off before a complete patch was ready. "
        "Try Fix with AI again.": (
            "Die KI-Antwort wurde abgeschnitten, bevor ein vollständiger Patch "
            "fertig war. Versuchen Sie Mit KI beheben erneut."
        ),
        "The AI proposed a patch that does not match the publication file. "
        "Try Fix with AI again.": (
            "Die KI hat einen Patch vorgeschlagen, der nicht zur Publikationsdatei "
            "passt. Versuchen Sie Mit KI beheben erneut."
        ),
        "The proposed patch has an empty original string.": (
            "Der vorgeschlagene Patch hat eine leere Originalzeichenfolge."
        ),
        "Could not apply the fix: the original text was not found in the file "
        "(it may have changed).": (
            "Korrektur nicht anwendbar: der Originaltext wurde in der Datei "
            "nicht gefunden (er könnte sich geändert haben)."
        ),
        "Could not apply the fix: the original text appears more than once "
        "in the file.": (
            "Korrektur nicht anwendbar: der Originaltext kommt in der Datei "
            "mehrfach vor."
        ),
        "The publication path is missing or no longer exists.": (
            "Der Publikationspfad fehlt oder existiert nicht mehr."
        ),
        "Could not find the file to edit inside the publication.": (
            "Die zu bearbeitende Datei wurde in der Publikation nicht gefunden."
        ),
        "This publication type cannot be edited in place by CheckMate.": (
            "Dieser Publikationstyp kann von CheckMate nicht vor Ort "
            "bearbeitet werden."
        ),
        "Could not write the fixed publication.": (
            "Die korrigierte Publikation konnte nicht geschrieben werden."
        ),
        "The publication package could not be read or rebuilt.": (
            "Das Publikationspaket konnte nicht gelesen oder neu erstellt werden."
        ),
        "What this means": "Was das bedeutet",
        "Why it matters": "Warum es wichtig ist",
        "Where in the file": "Wo in der Datei",
        "How to fix": "So beheben Sie es",
        "Learn more": "Mehr erfahren",
        "Model:": "Modell:",
        "AI model": "KI-Modell",
        "AI model selected in FIDO (read-only)": (
            "In FIDO ausgewähltes KI-Modell (schreibgeschützt)"
        ),
        "(no model selected)": "(kein Modell ausgewählt)",
        "View in browser": "Im Browser anzeigen",
        "Open the explanation in your web browser": (
            "Erklärung im Webbrowser öffnen"
        ),
        "Save as HTML…": "Als HTML speichern…",
        "Save the explanation as an HTML file": (
            "Erklärung als HTML-Datei speichern"
        ),
        "Save as Markdown…": "Als Markdown speichern…",
        "Save the explanation as a Markdown file": (
            "Erklärung als Markdown-Datei speichern"
        ),
        "Copy to clipboard": "In die Zwischenablage kopieren",
        "Copy the explanation markdown to the clipboard": (
            "Markdown-Erklärung in die Zwischenablage kopieren"
        ),
        "Save AI explanation as HTML": "KI-Erklärung als HTML speichern",
        "Save AI explanation as Markdown": (
            "KI-Erklärung als Markdown speichern"
        ),
        "Markdown files (*.md)|*.md;*.markdown|All files (*.*)|*.*": (
            "Markdown-Dateien (*.md)|*.md;*.markdown|Alle Dateien (*.*)|*.*"
        ),
        "Opened in browser.": "Im Browser geöffnet.",
        "Saved to {path}": "Gespeichert unter {path}",
        "Copied to clipboard.": "In die Zwischenablage kopiert.",
        "Copied to clipboard": "In die Zwischenablage kopiert",
        "The explanation was copied to the clipboard.": (
            "Die Erklärung wurde in die Zwischenablage kopiert."
        ),
        "AI status": "KI-Status",
        "Could not copy to the clipboard.": (
            "Konnte nicht in die Zwischenablage kopiert werden."
        ),
        "Close": "Schließen",
        "Could not open the explanation in a browser:\n{error}": (
            "Erklärung konnte nicht im Browser geöffnet werden:\n{error}"
        ),
        "Could not save the explanation:\n{error}": (
            "Erklärung konnte nicht gespeichert werden:\n{error}"
        ),
        "Ask AI to explain this issue in plain language "
        "(uses FIDO AI settings)": (
            "KI bitten, dieses Problem in verständlicher Sprache zu erklären "
            "(verwendet FIDO-KI-Einstellungen)"
        ),
        "AI explanation": "KI-Erklärung",
        "Follow-up question": "Nachfrage",
        "Ask a follow-up question…": "Stellen Sie eine Nachfrage…",
        "Ask": "Fragen",
        "Explaining…": "Wird erklärt…",
        "Thinking…": "Nachdenken…",
        "Done": "Fertig",
        "This explanation was generated by AI and may contain mistakes!": (
            "Diese Erklärung wurde von einer KI erzeugt und kann Fehler enthalten!"
        ),
        "Follow-up": "Nachfrage",
        "You asked": "Ihre Frage",
        "Could not explain this issue.": "Dieses Problem konnte nicht erklärt werden.",
        "AI support is not available (litellm is not installed).": (
            "KI-Unterstützung ist nicht verfügbar (litellm ist nicht installiert)."
        ),
        "No AI credentials found. Configure API keys or an unlock code in FIDO.": (
            "Keine KI-Zugangsdaten gefunden. Konfigurieren Sie API-Schlüssel "
            "oder einen Freischaltcode in FIDO."
        ),
        "No API key is available for the selected AI model. Check FIDO settings or your unlock code.": (
            "Für das ausgewählte KI-Modell ist kein API-Schlüssel verfügbar. "
            "Prüfen Sie die FIDO-Einstellungen oder Ihren Freischaltcode."
        ),
        "No AI model is selected in FIDO settings.": (
            "In den FIDO-Einstellungen ist kein KI-Modell ausgewählt."
        ),
        "The AI services unlock code was not found. Check the code in FIDO.": (
            "Der Freischaltcode für KI-Dienste wurde nicht gefunden. "
            "Prüfen Sie den Code in FIDO."
        ),
        "Could not reach the unlock server or AI provider. Check your connection.": (
            "Der Freischaltserver oder KI-Anbieter konnte nicht erreicht werden. "
            "Prüfen Sie Ihre Verbindung."
        ),
        "The unlock server returned invalid data.": (
            "Der Freischaltserver hat ungültige Daten zurückgegeben."
        ),
        "The AI services unlock code has expired.": (
            "Der Freischaltcode für KI-Dienste ist abgelaufen."
        ),
        "The unlock data could not be processed.": (
            "Die Freischaltdaten konnten nicht verarbeitet werden."
        ),
        "Could not refresh AI credentials from the unlock code.": (
            "KI-Zugangsdaten konnten anhand des Freischaltcodes "
            "nicht aktualisiert werden."
        ),
        "The unlock code did not provide usable API keys.": (
            "Der Freischaltcode hat keine nutzbaren API-Schlüssel geliefert."
        ),
        "The AI returned an empty response.": (
            "Die KI hat eine leere Antwort zurückgegeben."
        ),
        "Enter a follow-up question.": "Geben Sie eine Nachfrage ein.",
        "The AI provider returned an error.": (
            "Der KI-Anbieter hat einen Fehler zurückgegeben."
        ),
        "Explain the issue first, then ask a follow-up.": (
            "Erklären Sie zuerst das Problem und stellen Sie dann eine Nachfrage."
        ),
        "The AI request timed out. Try again, or check your connection and FIDO settings.": (
            "Die KI-Anfrage ist abgelaufen. Versuchen Sie es erneut, oder prüfen Sie "
            "Ihre Verbindung und die FIDO-Einstellungen."
        ),
        "The AI request was cancelled.": "Die KI-Anfrage wurde abgebrochen.",
        "Checking AI credentials…": "KI-Zugangsdaten werden geprüft…",
        "Checking AI connection…": "KI-Verbindung wird geprüft…",
        "Loading AI libraries…": "KI-Bibliotheken werden geladen…",
        "Could not load AI libraries.": "KI-Bibliotheken konnten nicht geladen werden.",
        "Cancelling…": "Wird abgebrochen…",
        "Cancelled.": "Abgebrochen.",
        "Open debugging &log…": "&Debug-Protokoll öffnen…",
        "No debugging log has been written yet.": (
            "Es wurde noch kein Debug-Protokoll geschrieben."
        ),
        "Debugging log": "Debug-Protokoll",
        "Could not open the debugging log:\n{path}": (
            "Das Debug-Protokoll konnte nicht geöffnet werden:\n{path}"
        ),
        "Continuing truncated reply…": "Gekürzte Antwort wird fortgesetzt…",
        "\n\n---\n*Note: The AI reply was cut off again. "
        "Ask a follow-up such as “Please continue.”*": (
            "\n\n---\n*Hinweis: Die KI-Antwort wurde erneut abgeschnitten. "
            "Stellen Sie eine Nachfrage wie „Bitte fortsetzen.“*"
        ),
    },
    LANG_PT: {
        "eBraille Checker": "eBraille Checker",
        "CheckMate": "CheckMate",
        "Publication": "Publicação",
        "Path:": "Caminho:",
        "Select or drop a .ebrl / .epub / .pdf file or folder — "
        "checking starts automatically": (
            "Selecione ou solte um ficheiro .ebrl / .epub / .pdf ou uma pasta — "
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
        "Source:": "Origem:",
        "Issue source filter": "Filtro por verificador",
        "EPUBCheck + Ace": "EPUBCheck + Ace",
        "Show issues from a specific checker, or all": (
            "Mostrar problemas de um verificador específico, ou de todos"
        ),
        "All issues": "Todos os problemas",
        "Errors only": "Apenas erros",
        "Warnings only": "Apenas avisos",
        "Info / usage": "Info / utilização",
        "Show one example of each issue": (
            "Mostrar um exemplo de cada problema"
        ),
        "&Copy summary": "&Copiar resumo",
        "Copy the result summary (Ctrl+Shift+C)": (
            "Copiar o resumo do resultado (Ctrl+Shift+C)"
        ),
        "&Report…": "&Relatório…",
        "View or save reports, AI overview (when available), "
        "copy the summary, or view the full log": (
            "Ver ou guardar relatórios, visão geral de IA (quando disponível), "
            "copiar o resumo ou ver o registo completo"
        ),
        "Issues list": "Lista de problemas",
        "Severity": "Gravidade",
        "Occurrences": "Ocorrências",
        "Code": "Código",
        "Location": "Localização",
        "Message": "Mensagem",
        "Issue details": "Detalhes do problema",
        "Severity: {value}": "Gravidade: {value}",
        "Code: {value}": "Código: {value}",
        "(none)": "(nenhum)",
        "Press Enter or double-click an issue to read the full details.": (
            "Prima Enter ou faça duplo clique num problema para ler "
            "todos os detalhes."
        ),
        "Issues hint": "Dica de problemas",
        "Note": "Nota",
        "Full checker log": "Registo completo",
        "The log is empty.": "O registo está vazio.",
        "&File": "&Ficheiro",
        "Select &file…\tCtrl+O": "Selecionar &ficheiro…\tCtrl+O",
        "Select f&older…\tCtrl+Shift+O": "Selecionar &pasta…\tCtrl+Shift+O",
        "&Report": "&Relatório",
        "View &text report\tCtrl+T": "Ver relatório de &texto\tCtrl+T",
        "Save &text report…\tCtrl+Shift+S": (
            "Guardar relatório de &texto…\tCtrl+Shift+S"
        ),
        "View &HTML report in browser\tCtrl+H": (
            "Ver relatório &HTML no navegador\tCtrl+H"
        ),
        "AI &overview…\tCtrl+Shift+A": "Visão geral de &IA…\tCtrl+Shift+A",
        "Save &HTML report…\tCtrl+S": "Guardar relatório &HTML…\tCtrl+S",
        "Save &HTML report…": "Guardar relatório &HTML…",
        "E&xit\tEsc": "&Sair\tEsc",
        "&Copy summary\tCtrl+Shift+C": "&Copiar resumo\tCtrl+Shift+C",
        "C&lear results\tCtrl+Shift+N": "&Limpar resultados\tCtrl+Shift+N",
        "C&lear results": "&Limpar resultados",
        "A check is already running. Wait for it to finish, then clear.": (
            "Já está a decorrer uma verificação. Aguarde que termine e depois limpe."
        ),
        "&Tools": "&Ferramentas",
        "&Re-check publication\tF5": "&Verificar novamente\tF5",
        "Show &issues always": "Mostrar &problemas sempre",
        "When checked, open the issues list automatically after a check "
        "that finds issues (instead of pressing Show issues)": (
            "Se marcado, abre automaticamente a lista de problemas após uma "
            "verificação que encontre problemas (sem premir Mostrar problemas)"
        ),
        "View full &log\tCtrl+L": "Ver registo &completo\tCtrl+L",
        "Check for &updates…": "Procurar &atualizações…",
        "&Download / reinstall checkers…": "&Descarregar / reinstalar os verificadores…",
        "&Language": "&Idioma",
        "&Help": "A&juda",
        "&About": "&Acerca de",
        "Starting…": "A iniciar…",
        "Ready": "Pronto",
        "In the explanation: focus starts at the top; Tab moves between links. "
        "Tab after the last link, or Ctrl+Tab, moves to the next dialog control.": (
            "Na explicação: o foco começa no topo; Tab move-se entre ligações. "
            "Tab após a última ligação, ou Ctrl+Tab, passa para o controlo seguinte."
        ),
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
        "Drop a packaged .ebrl, .epub, or .pdf file, or an exploded "
        "eBraille/EPUB publication folder.": (
            "Solte um ficheiro .ebrl, .epub ou .pdf empacotado, ou uma pasta de "
            "publicação eBraille/EPUB descompactada."
        ),
        "Using first publication ({name}); ignored {count} other item(s).": (
            "A utilizar a primeira publicação ({name}); "
            "ignorado(s) {count} outro(s) item(ns)."
        ),
        "Multiple items": "Vários itens",
        "Select an eBraille, EPUB, or PDF publication": (
            "Selecionar uma publicação eBraille, EPUB ou PDF"
        ),
        "Publications (*.ebrl;*.epub;*.pdf)|"
        "*.ebrl;*.Ebrl;*.EBRL;*.epub;*.EPUB;*.pdf;*.PDF|"
        "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
        "EPUB (*.epub)|*.epub;*.EPUB|"
        "PDF (*.pdf)|*.pdf;*.PDF|"
        "All files (*.*)|*.*": (
            "Publications (*.ebrl;*.epub;*.pdf)|"
            "*.ebrl;*.Ebrl;*.EBRL;*.epub;*.EPUB;*.pdf;*.PDF|"
            "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
            "EPUB (*.epub)|*.epub;*.EPUB|"
            "PDF (*.pdf)|*.pdf;*.PDF|"
            "Todos os ficheiros (*.*)|*.*"
        ),
        "Select an exploded eBraille or EPUB publication folder": (
            "Selecionar uma pasta de publicação eBraille ou EPUB descompactada"
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
        "Nothing to view": "Nada a ver",
        "Save text report": "Guardar relatório de texto",
        "Save HTML report": "Guardar relatório HTML",
        "HTML files (*.html)|*.html;*.htm|All files (*.*)|*.*": (
            "Ficheiros HTML (*.html)|*.html;*.htm|Todos os ficheiros (*.*)|*.*"
        ),
        "Text files (*.txt)|*.txt|All files (*.*)|*.*": (
            "Ficheiros de texto (*.txt)|*.txt|Todos os ficheiros (*.*)|*.*"
        ),
        "Report saved to {path}": "Relatório guardado em {path}",
        "Opened HTML report in browser.": (
            "Relatório HTML aberto no navegador."
        ),
        "Could not open HTML report:\n{error}": (
            "Não foi possível abrir o relatório HTML:\n{error}"
        ),
        "Check report": "Relatório de verificação",
        "EPUBCheck report": "Relatório EPUBCheck",
        "eBraille Checker report": "Relatório eBraille Checker",
        "veraPDF report": "Relatório veraPDF",
        "Checker": "Verificador",
        "Date": "Data",
        "GUI version": "Versão da interface",
        "No issues listed.": "Nenhum problema listado.",
        "Generated by CheckMate": (
            "Gerado pelo CheckMate"
        ),
        "Skip to issues": "Ir para os problemas",
        "Issue counts": "Contagem de problemas",
        "All sources": "Todas as fontes",
        "Filter issues": "Filtrar problemas",
        "Search": "Pesquisar",
        "Search issues": "Pesquisar problemas",
        "Clear filters": "Limpar filtros",
        "Showing {visible} of {total}": "A mostrar {visible} de {total}",
        "No matching issues.": "Nenhum problema correspondente.",
        "{n} info": "{n} info",
        "{n} infos": "{n} infos",
        "{n} usage": "{n} utilização",
        "{n} usages": "{n} utilizações",
        "Checking for updates…": "A procurar atualizações…",
        "Update check failed": "Falha ao procurar atualizações",
        "Could not check for updates:\n{error}": (
            "Não foi possível procurar atualizações:\n{error}"
        ),
        "Up to date": "Atualizado",
        "You have the latest checkers.\n\n{detail}": (
            "Tem as versões mais recentes dos verificadores.\n\n{detail}"
        ),
        "Update available": "Atualização disponível",
        "New checker releases are available.\n\n"
        "{detail}\n\n"
        "Download and install them now?": (
            "Estão disponíveis novas versões dos verificadores.\n\n"
            "{detail}\n\n"
            "Descarregar e instalar agora?"
        ),
        "{name}\n  Installed: {installed}\n  Latest: {tag} — {label}": (
            "{name}\n  Instalada: {installed}\n  Mais recente: {tag} — {label}"
        ),
        "Download and reinstall the latest checkers now?\n\n{detail}": (
            "Descarregar e reinstalar as versões mais recentes agora?\n\n{detail}"
        ),
        "none": "nenhuma",
        "Fetching latest releases…": "A obter as versões mais recentes…",
        "Installing {tag}…": "A instalar {tag}…",
        "Installed": "Instalado",
        "Checkers installed successfully.\n\n{path}": (
            "Verificadores instalados com sucesso.\n\n{path}"
        ),
        "Install failed": "Falha na instalação",
        "Installation failed:\n{error}": "Falha na instalação:\n{error}",
        "An accessible, cross-platform front-end for the DAISY "
        "eBraille Checker, W3C EPUBCheck, and veraPDF (PDF/UA).": (
            "Uma interface acessível e multiplataforma para o "
            "verificador eBraille da DAISY, o EPUBCheck do W3C e o "
            "veraPDF (PDF/UA)."
        ),
        "EPUBCheck": "EPUBCheck",
        "veraPDF": "veraPDF",
        "About CheckMate": "Acerca do CheckMate",
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
        "{name} {version}": "{name} {version}",
        "{name} {version} (bundled)": "{name} {version} (incluído)",
        "{name} installed": "{name} instalado",
        "{name} not installed": "{name} não instalado",
        "Publication: {path}": "Publicação: {path}",
        "Checker: {name} {version}": "Verificador: {name} {version}",
        "Checker: {name}": "Verificador: {name}",
        "Date: {when}": "Data: {when}",
        "Parser": "Analisador",
        "Build date": "Data de compilação",
        "Processing time": "Tempo de processamento",
        "Validation profile": "Perfil de validação",
        "Total rules in profile": "Regras totais no perfil",
        "Passed checks": "Verificações aprovadas",
        "Failed checks": "Verificações falhadas",
        "Java not found": "Java não encontrado",
        "Language changed to {language}.": "Idioma alterado para {language}.",
        "Explain with AI": "Explicar com IA",
        "AI assistance": "Assistência de IA",
        "AI overview": "Visão geral de IA",
        "Overall assessment": "Avaliação geral",
        "Main themes": "Temas principais",
        "Suggested priorities": "Prioridades sugeridas",
        "Practical next steps": "Próximos passos práticos",
        "Caveats": "Avisos",
        "Writing overview…": "A redigir a visão geral…",
        "Nothing to overview": "Nada a resumir",
        "Save AI overview as HTML": "Guardar visão geral de IA como HTML",
        "Save AI overview as Markdown": "Guardar visão geral de IA como Markdown",
        "A check is already running. Wait for it to finish, then try again.": (
            "Já há uma verificação em curso. Aguarde que termine e tente "
            "novamente."
        ),
        "Opening issue details…": "A abrir os detalhes do problema…",
        "Loading AI view…": "A carregar a vista de IA…",
        "Suggest fix with AI": "Sugerir correção com IA",
        "Suggest all like this…": "Sugerir todos assim…",
        "Ask AI to suggest a minimal markup fix for this EPUB "
        "or eBraille issue (uses FIDO AI settings)": (
            "Pedir à IA uma correção mínima de marcação para este "
            "problema de EPUB ou eBraille (usa as definições de IA do FIDO)"
        ),
        "Ask AI to suggest unique fixes for every issue with the "
        "same checker code in this report (uses FIDO AI settings)": (
            "Pedir à IA correções únicas para cada problema com o mesmo "
            "código neste relatório (usa as definições de IA do FIDO)"
        ),
        "Apply fix and validate": "Aplicar correção e validar",
        "Write the proposed fix into the publication "
        "(creates a .bak backup first)": (
            "Escrever a correção proposta na publicação "
            "(cria primeiro uma cópia .bak)"
        ),
        "Write the proposed fix into the publication, "
        "then re-check and confirm whether the issue is resolved": (
            "Escrever a correção proposta na publicação, "
            "depois voltar a verificar e confirmar se o problema ficou resolvido"
        ),
        "Write the proposed fix into the publication, "
        "then re-check automatically": (
            "Escrever a correção proposta na publicação "
            "e voltar a verificar automaticamente"
        ),
        "Proposed fix": "Correção proposta",
        "Before": "Antes",
        "After": "Depois",
        "File": "Ficheiro",
        "(no rationale)": "(sem justificação)",
        "Suggesting fix…": "A sugerir correção…",
        "Suggesting fixes…": "A sugerir correções…",
        "Applying fix…": "A aplicar correção…",
        "Could not propose a fix.": "Não foi possível propor uma correção.",
        "Could not apply the fix.": "Não foi possível aplicar a correção.",
        "Fix suggested. Review, then Apply fix and validate.": (
            "Correção sugerida. Reveja e depois aplique e valide a correção."
        ),
        "Batch fix suggested ({n} patch(es)). Review, then "
        "Apply fix and validate.": (
            "Correção em lote sugerida ({n} correção(ões)). Reveja e depois "
            "aplique e valide a correção."
        ),
        "This proposal covers {n} text replacement(s) for {m} matching issue(s).": (
            "Esta proposta cobre {n} substituição(ões) de texto para "
            "{m} problema(s) correspondente(s)."
        ),
        "Patch": "Correção",
        "Skipped": "Ignorados",
        "All matching issues with code {code} appear to be resolved "
        "({before} → {after}).": (
            "Todos os problemas com o código {code} parecem resolvidos "
            "({before} → {after})."
        ),
        "Matching issues with code {code}: {before} before, "
        "{after} after the batch fix.": (
            "Problemas com o código {code}: {before} antes, "
            "{after} depois da correção em lote."
        ),
        "Patches applied: {n}.": "Correções aplicadas: {n}.",
        "Fix applied": "Correção aplicada",
        "Fix applied. Re-check the publication with F5.": (
            "Correção aplicada. Volte a verificar a publicação com F5."
        ),
        "Fix applied. Backup: {path}. Re-check with F5.": (
            "Correção aplicada. Cópia de segurança: {path}. "
            "Volte a verificar com F5."
        ),
        "The issue appears to be resolved after applying the fix.": (
            "O problema parece resolvido após aplicar a correção."
        ),
        "The targeted issue appears to be resolved (code: {code}).": (
            "O problema em causa parece resolvido (código: {code})."
        ),
        "The targeted issue is still reported after the fix was applied "
        "(code: {code}).": (
            "O problema em causa continua a ser reportado após aplicar a "
            "correção (código: {code})."
        ),
        "Totals before: {fatals} fatal(s), {errors} error(s), "
        "{warnings} warning(s).": (
            "Totais antes: {fatals} fatal(is), {errors} erro(s), "
            "{warnings} aviso(s)."
        ),
        "Totals after: {fatals} fatal(s), {errors} error(s), "
        "{warnings} warning(s).": (
            "Totais depois: {fatals} fatal(is), {errors} erro(s), "
            "{warnings} aviso(s)."
        ),
        "Overall errors/warnings decreased.": (
            "O total de erros/avisos diminuiu."
        ),
        "Overall errors/warnings did not decrease after the fix.": (
            "O total de erros/avisos não diminuiu após a correção."
        ),
        "Fixing this Ace issue introduced {n} new EPUBCheck "
        "error(s) that were not present before:": (
            "Corrigir este problema do Ace introduziu {n} novo(s) erro(s) "
            "do EPUBCheck que não existiam antes:"
        ),
        "No new EPUBCheck errors were introduced by this Ace fix.": (
            "Esta correção do Ace não introduziu novos erros do EPUBCheck."
        ),
        "…and {n} more.": "…e mais {n}.",
        "No backup file was found to revert.": (
            "Não foi encontrado nenhum ficheiro de cópia de segurança para reverter."
        ),
        "Do you want to revert to the backup?\n\n"
        "Backup:\n{backup}": (
            "Pretende reverter para a cópia de segurança?\n\n"
            "Cópia de segurança:\n{backup}"
        ),
        "Fix confirmed": "Correção confirmada",
        "Edit log:\n{path}": "Registo de edições:\n{path}",
        "Edit changelog": "Registo de edições",
        "View edit &changelog…\tCtrl+Shift+G": (
            "Ver &registo de edições…\tCtrl+Shift+G"
        ),
        "Open in &browser": "Abrir no &navegador",
        "Open &folder": "Abrir &pasta",
        "No changelog": "Sem registo",
        "No CheckMate edit changelog was found for this publication.\n\n"
        "A changelog is created beside the file (or inside an exploded "
        "folder) when you apply an AI fix.": (
            "Não foi encontrado um registo de edições do CheckMate para esta "
            "publicação.\n\n"
            "Um registo é criado junto ao ficheiro (ou dentro de uma pasta "
            "expandida) quando aplica uma correção com IA."
        ),
        "Could not read the changelog:\n{error}": (
            "Não foi possível ler o registo de edições:\n{error}"
        ),
        "Could not open the changelog in a browser:\n{error}": (
            "Não foi possível abrir o registo no navegador:\n{error}"
        ),
        "Could not open the folder:\n{error}": (
            "Não foi possível abrir a pasta:\n{error}"
        ),
        "Open the CheckMate edit changelog for this publication "
        "(AI fixes and backups), when one exists": (
            "Abrir o registo de edições do CheckMate para esta publicação "
            "(correções IA e cópias de segurança), se existir"
        ),
        "Open a formatted HTML view in your browser": (
            "Abrir uma vista HTML formatada no navegador"
        ),
        "Reveal the changelog file in the file manager": (
            "Mostrar o ficheiro de registo no gestor de ficheiros"
        ),
        "Fix not confirmed": "Correção não confirmada",
        "Re-check failed": "Falha na nova verificação",
        "Reverted": "Revertido",
        "The publication was reverted to the backup.": (
            "A publicação foi revertida para a cópia de segurança."
        ),
        "Could not revert to the backup:\n{detail}": (
            "Não foi possível reverter para a cópia de segurança:\n{detail}"
        ),
        "Do you want to revert to the backup created before the fix?": (
            "Pretende reverter para a cópia de segurança criada antes da correção?"
        ),
        "The publication was changed, but the re-check could not be completed.\n\n"
        "{detail}": (
            "A publicação foi alterada, mas a nova verificação não pôde "
            "ser concluída.\n\n{detail}"
        ),
        "The issue is still reported after the fix was applied "
        "(code: {code}).\n\n"
        "No backup file was found to revert.": (
            "O problema continua a ser reportado após aplicar a correção "
            "(código: {code}).\n\n"
            "Não foi encontrado nenhum ficheiro de cópia de segurança para reverter."
        ),
        "The issue is still reported after the fix was applied "
        "(code: {code}).\n\n"
        "Do you want to revert to the backup?\n\n"
        "Backup:\n{backup}": (
            "O problema continua a ser reportado após aplicar a correção "
            "(código: {code}).\n\n"
            "Pretende reverter para a cópia de segurança?\n\n"
            "Cópia de segurança:\n{backup}"
        ),
        "Apply this fix to the publication?\n\n"
        "File: {file}\n\n"
        "A .bak backup will be created first. "
        "Re-check the publication (F5) afterward to verify.": (
            "Aplicar esta correção à publicação?\n\n"
            "Ficheiro: {file}\n\n"
            "Será criada primeiro uma cópia .bak. "
            "Depois, volte a verificar a publicação (F5)."
        ),
        "Fix with AI is only available for EPUB and eBraille publications.": (
            "Corrigir com IA só está disponível para publicações "
            "EPUB e eBraille."
        ),
        "The AI did not return an applicable patch. You can still read the "
        "reply above, or try Explain with AI.": (
            "A IA não devolveu uma correção aplicável. Tente novamente "
            "Corrigir com IA, ou use Explicar com IA."
        ),
        "The AI did not return an applicable patch. Try Fix with AI again, "
        "or use Explain with AI.": (
            "A IA não devolveu uma correção aplicável. Tente novamente "
            "Corrigir com IA, ou use Explicar com IA."
        ),
        "The AI reply was incomplete or unusable (draft text or invalid JSON). "
        "Try Fix with AI again.": (
            "A resposta da IA estava incompleta ou inutilizável (rascunho "
            "ou JSON inválido). Tente novamente Corrigir com IA."
        ),
        "The AI reply was cut off before a complete patch was ready. "
        "Try Fix with AI again.": (
            "A resposta da IA foi cortada antes de uma correção completa. "
            "Tente novamente Corrigir com IA."
        ),
        "The AI proposed a patch that does not match the publication file. "
        "Try Fix with AI again.": (
            "A IA propôs uma correção que não corresponde ao ficheiro da "
            "publicação. Tente novamente Corrigir com IA."
        ),
        "The proposed patch has an empty original string.": (
            "A correção proposta tem uma cadeia original vazia."
        ),
        "Could not apply the fix: the original text was not found in the file "
        "(it may have changed).": (
            "Não foi possível aplicar a correção: o texto original "
            "não foi encontrado no ficheiro (pode ter mudado)."
        ),
        "Could not apply the fix: the original text appears more than once "
        "in the file.": (
            "Não foi possível aplicar a correção: o texto original "
            "aparece mais do que uma vez no ficheiro."
        ),
        "The publication path is missing or no longer exists.": (
            "O caminho da publicação está em falta ou já não existe."
        ),
        "Could not find the file to edit inside the publication.": (
            "Não foi possível encontrar o ficheiro a editar na publicação."
        ),
        "This publication type cannot be edited in place by CheckMate.": (
            "Este tipo de publicação não pode ser editado no local "
            "pelo CheckMate."
        ),
        "Could not write the fixed publication.": (
            "Não foi possível escrever a publicação corrigida."
        ),
        "The publication package could not be read or rebuilt.": (
            "O pacote da publicação não pôde ser lido ou reconstruído."
        ),
        "What this means": "O que isto significa",
        "Why it matters": "Porque é importante",
        "Where in the file": "Onde no ficheiro",
        "How to fix": "Como corrigir",
        "Learn more": "Saber mais",
        "Model:": "Modelo:",
        "AI model": "Modelo de IA",
        "AI model selected in FIDO (read-only)": (
            "Modelo de IA selecionado no FIDO (só de leitura)"
        ),
        "(no model selected)": "(nenhum modelo selecionado)",
        "View in browser": "Ver no navegador",
        "Open the explanation in your web browser": (
            "Abrir a explicação no navegador web"
        ),
        "Save as HTML…": "Guardar como HTML…",
        "Save the explanation as an HTML file": (
            "Guardar a explicação como ficheiro HTML"
        ),
        "Save as Markdown…": "Guardar como Markdown…",
        "Save the explanation as a Markdown file": (
            "Guardar a explicação como ficheiro Markdown"
        ),
        "Copy to clipboard": "Copiar para a área de transferência",
        "Copy the explanation markdown to the clipboard": (
            "Copiar a explicação Markdown para a área de transferência"
        ),
        "Save AI explanation as HTML": "Guardar explicação da IA como HTML",
        "Save AI explanation as Markdown": (
            "Guardar explicação da IA como Markdown"
        ),
        "Markdown files (*.md)|*.md;*.markdown|All files (*.*)|*.*": (
            "Ficheiros Markdown (*.md)|*.md;*.markdown|Todos os ficheiros (*.*)|*.*"
        ),
        "Opened in browser.": "Aberto no navegador.",
        "Saved to {path}": "Guardado em {path}",
        "Copied to clipboard.": "Copiado para a área de transferência.",
        "Copied to clipboard": "Copiado para a área de transferência",
        "The explanation was copied to the clipboard.": (
            "A explicação foi copiada para a área de transferência."
        ),
        "AI status": "Estado da IA",
        "Could not copy to the clipboard.": (
            "Não foi possível copiar para a área de transferência."
        ),
        "Close": "Fechar",
        "Could not open the explanation in a browser:\n{error}": (
            "Não foi possível abrir a explicação num navegador:\n{error}"
        ),
        "Could not save the explanation:\n{error}": (
            "Não foi possível guardar a explicação:\n{error}"
        ),
        "Ask AI to explain this issue in plain language "
        "(uses FIDO AI settings)": (
            "Pedir à IA que explique este problema em linguagem simples "
            "(usa as definições de IA do FIDO)"
        ),
        "AI explanation": "Explicação da IA",
        "Follow-up question": "Pergunta de seguimento",
        "Ask a follow-up question…": "Faça uma pergunta de seguimento…",
        "Ask": "Perguntar",
        "Explaining…": "A explicar…",
        "Thinking…": "A pensar…",
        "Done": "Concluído",
        "This explanation was generated by AI and may contain mistakes!": (
            "Esta explicação foi gerada por IA e pode conter erros!"
        ),
        "Follow-up": "Seguimento",
        "You asked": "Você perguntou",
        "Could not explain this issue.": "Não foi possível explicar este problema.",
        "AI support is not available (litellm is not installed).": (
            "A assistência de IA não está disponível (litellm não está instalado)."
        ),
        "No AI credentials found. Configure API keys or an unlock code in FIDO.": (
            "Não foram encontradas credenciais de IA. Configure chaves API "
            "ou um código de desbloqueio no FIDO."
        ),
        "No API key is available for the selected AI model. Check FIDO settings or your unlock code.": (
            "Não há chave API disponível para o modelo de IA selecionado. "
            "Verifique as definições do FIDO ou o seu código de desbloqueio."
        ),
        "No AI model is selected in FIDO settings.": (
            "Nenhum modelo de IA está selecionado nas definições do FIDO."
        ),
        "The AI services unlock code was not found. Check the code in FIDO.": (
            "O código de desbloqueio dos serviços de IA não foi encontrado. "
            "Verifique o código no FIDO."
        ),
        "Could not reach the unlock server or AI provider. Check your connection.": (
            "Não foi possível contactar o servidor de desbloqueio ou o "
            "fornecedor de IA. Verifique a sua ligação."
        ),
        "The unlock server returned invalid data.": (
            "O servidor de desbloqueio devolveu dados inválidos."
        ),
        "The AI services unlock code has expired.": (
            "O código de desbloqueio dos serviços de IA expirou."
        ),
        "The unlock data could not be processed.": (
            "Os dados de desbloqueio não puderam ser processados."
        ),
        "Could not refresh AI credentials from the unlock code.": (
            "Não foi possível atualizar as credenciais de IA a partir "
            "do código de desbloqueio."
        ),
        "The unlock code did not provide usable API keys.": (
            "O código de desbloqueio não forneceu chaves API utilizáveis."
        ),
        "The AI returned an empty response.": (
            "A IA devolveu uma resposta vazia."
        ),
        "Enter a follow-up question.": "Introduza uma pergunta de seguimento.",
        "The AI provider returned an error.": (
            "O fornecedor de IA devolveu um erro."
        ),
        "Explain the issue first, then ask a follow-up.": (
            "Explique primeiro o problema e depois faça uma pergunta de seguimento."
        ),
        "The AI request timed out. Try again, or check your connection and FIDO settings.": (
            "O pedido de IA expirou. Tente novamente, ou verifique a sua ligação "
            "e as definições do FIDO."
        ),
        "The AI request was cancelled.": "O pedido de IA foi cancelado.",
        "Checking AI credentials…": "A verificar credenciais de IA…",
        "Checking AI connection…": "A verificar ligação de IA…",
        "Loading AI libraries…": "A carregar bibliotecas de IA…",
        "Could not load AI libraries.": "Não foi possível carregar as bibliotecas de IA.",
        "Cancelling…": "A cancelar…",
        "Cancelled.": "Cancelado.",
        "Open debugging &log…": "Abrir o registo de &depuração…",
        "No debugging log has been written yet.": (
            "Ainda não foi escrito nenhum registo de depuração."
        ),
        "Debugging log": "Registo de depuração",
        "Could not open the debugging log:\n{path}": (
            "Não foi possível abrir o registo de depuração:\n{path}"
        ),
        "Continuing truncated reply…": "A continuar a resposta truncada…",
        "\n\n---\n*Note: The AI reply was cut off again. "
        "Ask a follow-up such as “Please continue.”*": (
            "\n\n---\n*Nota: a resposta da IA foi cortada outra vez. "
            "Faça uma pergunta de seguimento como «Por favor, continue.»*"
        ),
    },
}

# Danish, Dutch, Finnish, Hindi, Norwegian, Russian, Swedish
from .i18n_extra import EXTRA_TRANSLATIONS

_TRANSLATIONS.update(EXTRA_TRANSLATIONS)

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
                0x06: LANG_DA,  # Danish
                0x13: LANG_NL,  # Dutch
                0x0B: LANG_FI,  # Finnish
                0x39: LANG_HI,  # Hindi
                0x14: LANG_NB,  # Norwegian → Bokmål
                0x19: LANG_RU,  # Russian
                0x1D: LANG_SV,  # Swedish
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
        if code.startswith("da"):
            return LANG_DA
        if code.startswith("nl"):
            return LANG_NL
        if code.startswith("fi"):
            return LANG_FI
        if code.startswith("hi"):
            return LANG_HI
        # Norwegian: nb (Bokmål), nn (Nynorsk), no (macrolanguage) → nb catalog
        if code.startswith("nb") or code.startswith("nn") or code.startswith("no"):
            return LANG_NB
        if code.startswith("ru"):
            return LANG_RU
        if code.startswith("sv"):
            return LANG_SV
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


def language_display_name(lang: str | None = None) -> str:
    """English language name for AI prompts (based on UI language)."""
    code = lang if lang is not None else _current_language
    return LANGUAGE_DISPLAY_NAMES.get(code, "English")


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
