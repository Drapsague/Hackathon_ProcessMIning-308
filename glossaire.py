"""Termes affichés dans l'onglet Glossary : (thème, terme, définition)."""

GLOSSAIRE = [
    ("Data sources", "MES", "Manufacturing Execution System. Production log: one row per operation with station, "
     "planned time, actual time, start and end times, declared incident and cause, consumed references."),
    ("Data sources", "PLM", "Product Lifecycle Management. Parts master: designation, bill of materials quantity, "
     "supplier, supply lead time, criticality, mass, purchase cost."),
    ("Data sources", "ERP", "Enterprise Resource Planning. Here, the team list: employee ID, assembly station, "
     "qualification, experience level, skills, certifications, hourly cost, rotation. First names, last names and "
     "career comments are not loaded by the tool."),
    ("Data sources", "Assembly manual", "Meccano Dreamliner assembly manual: 56 steps, transcribed with their page "
     "and parts list, plus the inventory on page 43."),
    ("Data sources", "3D model (CAD)", "STEP model \"Boeing 787 A_\" converted for display. References are read from "
     "instance names, never inferred from the shape."),
    ("Data sources", "MSN-001", "Manufacturer Serial Number: serial number of the only real aircraft in the MES."),
    ("Data sources", "Evidence (MES:L32, PLM:D145, ERP:P31…)", "Identifier of the data behind a figure: MES file "
     "row, PLM reference, ERP station, manual page (NOTICE:p25-e31), quality check (DQ-01) or calculation "
     "(STAT:…)."),
    ("Data sources", "Source files", "The tool reads the English (UK) MES, PLM and ERP extracts when they are in "
     "the data folder, otherwise the French (FR) ones. Both give the same figures; free text (operation names, "
     "incidents, designations) is shown as extracted."),

    ("Production", "Station", "Step of the assembly routing, numbered 1 to 56 in MES order."),
    ("Production", "Planned time / actual time", "Scheduled and declared duration of an operation, in minutes."),
    ("Production", "Gap", "Actual time - planned time, in minutes. Positive: the operation overran."),
    ("Production", "Delay (%)", "(Actual time - planned time) / planned time × 100."),
    ("Production", "Delay severity", "None if the delay is zero or negative, minor below 20%, major from 20% to "
     "under 50%, critical from 50%."),
    ("Production", "Cumulative overrun", "Sum of max(0, actual time - planned time) over operations: early "
     "finishes do not offset delays."),
    ("Production", "Declared incident", "Event recorded in the MES during the operation, with a potential cause. "
     "It is not verified."),
    ("Production", "Bottleneck", "Station that limits line throughput: here, the one that exceeds its planned time "
     "the most."),
    ("Production", "MES05 · time consistency", "Checks that end time - start time matches the declared actual time, "
     "within 1 minute. Stations 15, 31 and 50 fail: their end is capped at 17:00."),

    ("Score and priorities", "Investigation score (P04)", "Delay (0, 10, 20 or 40 points depending on severity) + "
     "declared incident (25) + critical part (20) + long lead-time part (15), capped at 100. It says where to look, "
     "not why."),
    ("Score and priorities", "Priority", "Low from 0 to 29, medium from 30 to 59, high from 60 to 100."),
    ("Score and priorities", "Provisional score", "Score computed with missing data (for example an operation with "
     "no part reference): missing data is not zero risk."),
    ("Score and priorities", "CROSS signals", "Cross-checks between sources: delay + critical part (CROSS01), delay + "
     "long lead time (CROSS02), delay + incident (CROSS03), delay + ERP resources (CROSS04). They are leads, never "
     "causes."),

    ("Parts", "Reference", "Part code shared by the MES, the PLM and the CAD model (for example D145)."),
    ("Parts", "Bill of materials", "List of parts and quantities needed for one aircraft, according to the PLM."),
    ("Parts", "Criticality", "PLM attribute: low, medium, high or critical."),
    ("Parts", "Supply lead time", "Range in days given by the PLM. The tool uses the upper bound."),
    ("Parts", "Q3", "Third quartile of lead times: 75% of references have a lead time less than or equal to it. "
     "Above it, the lead time is called \"long\" (rule PLM02)."),

    ("Teams", "Employee ID", "ERP identifier of a resource (AIR0001…). An ID is assigned to a station; the MES does "
     "not say which one carried out a given operation."),
    ("Teams", "Station headcount", "Number of employee IDs declared on the station in the ERP, not the number of "
     "people present."),
    ("Teams", "Rotation", "Stations where a resource is scheduled in other weeks, according to the ERP."),

    ("Finance", "FIN01 · total parts cost", "Sum over references of PLM quantity × purchase cost."),
    ("Finance", "FIN02 · cost by reference", "Quantity × unit cost for one reference."),
    ("Finance", "FIN03 · budget share", "Share of a reference in FIN01, and cumulative share with references ranked "
     "from most to least expensive."),
    ("Finance", "FIN04 · station theoretical hourly cost", "Sum of the hourly costs of the station's ERP resources, "
     "assuming they work at the same time."),
    ("Finance", "FIN05 · average hourly cost", "Average hourly cost of the resources."),
    ("Finance", "Reference cost", "Value taken from the PLM or the ERP. It is not a loss caused by delays."),

    ("AI assistant", "Analyst, Librarian, Writer, Checker", "The assistant's four steps: facts computed in Python, "
     "search for nearby evidence, drafting (template or LLM), check of every sentence."),
    ("AI assistant", "Hypothesis", "Interpretation sentence in italics, with no figures: it suggests a reading the "
     "data does not prove."),
    ("AI assistant", "Insufficient data", "Refusal answer when no evidence can support a reply."),
    ("AI assistant", "LLM", "Large Language Model, here through Groq. It rephrases the computed facts and computes "
     "nothing."),
    ("AI assistant", "Process mining", "Rebuilding the actual process from event logs (here the MES) to compare it "
     "with the planned process."),
]
