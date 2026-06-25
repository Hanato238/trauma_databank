#!/usr/bin/env python3
"""
Build correct flat abdomen JSON from image-analyzed data.
Corrects systematic parent-child hierarchy errors in existing JSON.

Usage: uv run build_abdomen_json.py
"""

import json
import re

JP_LOOKUP_PATH = "/workspace/scripts/jp_lookup.json"
OUTPUT_PATH = "/workspace/output/codebook/json_v2/abdomen.json"

# (code, english_description, ais_severity, hierarchy_level, parent_code_or_None)
# Data extracted directly from images abdomen_002 through abdomen_032
RAW_ENTRIES = [
    # ── IMAGE 002: Whole Area / Penetrating / Rectus / Torso / Skin+Sub ──────
    ("500099.9", "Injuries to the Whole Abdomen NFS",                                                                 9, 1, None),
    ("500999.9", "Died of abdominal injury without further substantiation of injuries or no autopsy confirmation of specific injuries", 9, 2, "500099.9"),

    ("516000.1", "Penetrating injury NFS",                                                                            1, 1, None),
    ("516002.1", "superficial ; minor ; into peritoneum but not involving underlying structures",                       1, 2, "516000.1"),
    ("516004.2", "with tissue loss >100cm²",                                                                           2, 2, "516000.1"),
    ("516006.3", "with blood loss >20% by volume",                                                                     3, 2, "516000.1"),

    ("510100.2", "Rectus Abdominus rupture NFS",                                                                       2, 1, None),
    ("511000.6", "Torso transection",                                                                                  6, 1, None),

    ("510099.1", "Skin/Subcutaneous/Muscle [except rectus abdominus] NFS",                                            1, 1, None),
    ("510202.1", "abrasion",                                                                                           1, 2, "510099.1"),
    ("510402.1", "contusion ; hematoma",                                                                               1, 2, "510099.1"),
    ("510600.1", "laceration NFS",                                                                                     1, 2, "510099.1"),
    ("510602.1", "minor ; superficial",                                                                                1, 3, "510600.1"),
    ("510604.2", "major ; >20cm long and into subcutaneous tissue",                                                    2, 3, "510600.1"),
    ("510606.3", "blood loss >20% by volume",                                                                          3, 3, "510600.1"),
    ("510800.1", "avulsion NFS",                                                                                       1, 2, "510099.1"),
    ("510802.1", "minor ; superficial ; ≤100cm²",                                                                      1, 3, "510800.1"),
    ("510804.2", "major ; >100cm²",                                                                                    2, 3, "510800.1"),
    ("510806.3", "blood loss >20% by volume",                                                                          3, 3, "510800.1"),

    # ── IMAGE 004: Vessels – Aorta / Celiac / Iliac Artery ───────────────────
    ("520099.9", "Vascular Injury in Abdomen NFS",                                                                     9, 1, None),

    ("520299.4", "Aorta, Abdominal NFS",                                                                               4, 1, None),
    ("520202.4", "intimal tear, no disruption",                                                                        4, 2, "520299.4"),
    ("520204.4", "laceration ; perforation ; puncture NFS",                                                            4, 2, "520299.4"),
    ("520206.4", "minor ; superficial ; incomplete circumferential involvement ; blood loss ≤20% by volume",           4, 3, "520204.4"),
    ("520208.5", "major ; rupture ; transection ; segmental loss ; blood loss >20% by volume",                        5, 3, "520204.4"),

    ("520499.3", "Celiac Artery NFS",                                                                                  3, 1, None),
    ("520402.3", "intimal tear ; no disruption",                                                                       3, 2, "520499.3"),
    ("520404.3", "laceration ; perforation ; puncture NFS",                                                            3, 2, "520499.3"),
    ("520406.4", "minor ; superficial ; incomplete circumferential involvement ; blood loss ≤20% by volume",           4, 3, "520404.3"),
    ("520408.5", "major ; rupture ; transection ; segmental loss ; blood loss >20% by volume",                        5, 3, "520404.3"),

    ("520699.3", "Iliac Artery [common, internal, external] and its named branches NFS",                               3, 1, None),
    ("520698.4", "bilateral (for common iliac artery only)",                                                           4, 2, "520699.3"),
    ("520602.3", "intimal tear, no disruption",                                                                        3, 2, "520699.3"),
    ("520604.3", "laceration ; perforation ; puncture NFS",                                                            3, 2, "520699.3"),
    ("520606.3", "minor ; superficial ; incomplete circumferential involvement ; blood loss ≤20% by volume",           3, 3, "520604.3"),
    ("520608.4", "major ; rupture ; transection ; segmental loss ; blood loss >20% by volume",                        4, 3, "520604.3"),

    # ── IMAGE 006: SMA / Other Arteries / Iliac Vein / Vena Cava ─────────────
    ("521199.3", "Superior Mesenteric Artery NFS",                                                                     3, 1, None),
    ("521102.3", "intimal tear, no disruption",                                                                        3, 2, "521199.3"),
    ("521104.3", "laceration ; perforation ; puncture NFS",                                                            3, 2, "521199.3"),
    ("521106.3", "minor ; superficial ; incomplete circumferential involvement ; blood loss ≤20% by volume",           3, 3, "521104.3"),
    ("521108.4", "major ; rupture ; transection ; segmental loss ; blood loss >20% by volume",                        4, 3, "521104.3"),

    ("521499.3", "Other named arteries NFS [e.g., hepatic, renal, splenic]",                                           3, 1, None),
    ("521402.3", "intimal tear, no disruption",                                                                        3, 2, "521499.3"),
    ("521404.3", "laceration ; perforation ; puncture NFS",                                                            3, 2, "521499.3"),
    ("521406.3", "minor ; superficial ; incomplete circumferential involvement ; blood loss ≤20% by volume",           3, 3, "521404.3"),
    ("521408.4", "major ; rupture ; transection ; segmental loss ; blood loss >20% by volume",                        4, 3, "521404.3"),

    ("520899.3", "Iliac Vein [common] NFS",                                                                            3, 1, None),
    ("520802.3", "laceration ; perforation ; puncture NFS",                                                            3, 2, "520899.3"),
    ("520804.3", "minor ; superficial ; incomplete circumferential involvement ; blood loss ≤20% by volume",           3, 3, "520802.3"),
    ("520806.4", "major ; rupture ; transection ; segmental loss ; blood loss >20% by volume",                        4, 3, "520802.3"),

    ("521099.2", "Iliac Vein [internal, external] NFS",                                                                2, 1, None),
    ("521002.2", "laceration ; perforation ; puncture NFS",                                                            2, 2, "521099.2"),
    ("521004.2", "minor ; superficial ; incomplete circumferential involvement ; blood loss ≤20% by volume",           2, 3, "521002.2"),
    ("521006.3", "major ; rupture ; transection ; segmental loss ; blood loss >20% by volume",                        3, 3, "521002.2"),

    ("521299.3", "Vena Cava, inferior NFS",                                                                            3, 1, None),
    ("521202.3", "laceration ; perforation ; puncture NFS",                                                            3, 2, "521299.3"),
    ("521204.3", "minor ; superficial ; incomplete circumferential involvement ; blood loss ≤20% by volume",           3, 3, "521202.3"),
    ("521206.4", "major ; rupture ; transection ; segmental loss ; blood loss >20% by volume",                        4, 3, "521202.3"),

    # ── IMAGE 008: Other Named Veins / Nerves ────────────────────────────────
    ("521699.3", "Other named veins NFS [e.g., portal, renal, splenic, superior mesenteric]",                          3, 1, None),
    ("521602.3", "laceration ; perforation ; puncture NFS",                                                            3, 2, "521699.3"),
    ("521604.3", "minor with or without thrombosis ; superficial ; incomplete circumferential involvement ; blood loss ≤20% by volume", 3, 3, "521602.3"),
    ("521606.4", "major ; rupture ; transection ; segmental loss ; blood loss >20% by volume",                        4, 3, "521602.3"),

    ("530499.1", "Vagus nerve injury",                                                                                 1, 1, None),

    # ── IMAGE 012: Adrenal / Anus / Appendix / Bladder ───────────────────────
    ("540299.1", "Adrenal Gland NFS",                                                                                  1, 1, None),
    ("540210.1", "contusion ; hematoma NFS [OIS I]",                                                                   1, 2, "540299.1"),
    ("540212.1", "minor ; superficial [OIS I]",                                                                        1, 3, "540210.1"),
    ("540214.2", "major ; large [OIS I]",                                                                              2, 3, "540210.1"),
    ("540220.1", "laceration NFS",                                                                                     1, 2, "540299.1"),
    ("540222.1", "minor ; superficial ; only cortex involvement ; <2cm [OIS II]",                                      1, 3, "540220.1"),
    ("540224.2", "major ; multiple lacerations ; extending into medulla ; ≥2cm [OIS III]",                             2, 3, "540220.1"),
    ("540226.3", "massive ; avulsion ; complex ; rupture ; >50% parenchymal destruction [OIS IV, V]",                  3, 3, "540220.1"),

    ("540499.1", "Anus NFS",                                                                                           1, 1, None),
    ("540410.1", "contusion ; hematoma",                                                                               1, 2, "540499.1"),
    ("540420.2", "laceration NFS",                                                                                     2, 2, "540499.1"),
    ("540422.2", "no perforation ; partial thickness",                                                                 2, 3, "540420.2"),
    ("540424.3", "perforation ; full thickness",                                                                       3, 3, "540420.2"),
    ("540426.4", "massive ; avulsion ; complex ; rupture ; major tissue loss",                                         4, 3, "540420.2"),

    ("540322.2", "Appendix laceration ; perforation",                                                                  2, 1, None),

    ("540699.2", "Bladder (urinary) NFS",                                                                              2, 1, None),
    ("540610.2", "contusion ; hematoma [OIS I]",                                                                       2, 2, "540699.2"),
    ("540620.2", "laceration NFS",                                                                                     2, 2, "540699.2"),
    ("540622.2", "no perforation ; partial thickness [OIS I]",                                                         2, 3, "540620.2"),
    ("540623.2", "extraperitoneal wall ≤2cm [OIS II]",                                                                 2, 3, "540620.2"),
    ("540624.3", "extraperitoneal wall >2cm ; intraperitoneal wall ≤2cm [OIS III]",                                    3, 3, "540620.2"),
    ("540625.3", "intraperitoneal wall >2cm [OIS IV]",                                                                 3, 3, "540620.2"),
    ("540626.4", "massive ; avulsion ; complex, tissue loss ; involving urethral orifice (trigone) or bladder neck [OIS V]", 4, 3, "540620.2"),
    ("540640.3", "rupture NFS",                                                                                        3, 2, "540699.2"),

    # ── IMAGE 014: Colon / Duodenum ──────────────────────────────────────────
    ("540899.2", "Colon (large bowel) NFS",                                                                            2, 1, None),
    ("540810.2", "contusion ; hematoma [OIS I]",                                                                       2, 2, "540899.2"),
    ("540820.2", "laceration NFS",                                                                                     2, 2, "540899.2"),
    ("540822.2", "no perforation ; partial thickness ; <50% circumference [OIS I, II]",                                2, 3, "540820.2"),
    ("540824.3", "perforation ; full thickness ; ≥50% circumference without transection ; multiple simple wounds [OIS III]", 3, 3, "540820.2"),
    ("540826.4", "massive ; avulsion ; complex ; tissue loss ; transection ; large areas of tissue devitalization or devascularization [OIS IV, V]", 4, 3, "540820.2"),

    ("541099.2", "Duodenum NFS",                                                                                       2, 1, None),
    ("541010.2", "contusion ; hematoma [OIS I, II]",                                                                   2, 2, "541099.2"),
    ("541020.2", "laceration NFS",                                                                                     2, 2, "541099.2"),
    ("541022.2", "no perforation ; partial thickness ; serosal tear [OIS I]",                                          2, 3, "541020.2"),
    ("541021.2", "disruption ; <50% circumference [OIS II]",                                                           2, 3, "541020.2"),
    ("541023.3", "disruption 50-100% circumference of D1 (superior or first part), D3 (transverse or third part) or D4 (ascending or fourth part) [OIS III]", 3, 3, "541020.2"),
    ("541025.3", "disruption 50-75% circumference of D2 (descending or second part) [OIS III]",                       3, 3, "541020.2"),
    ("541024.4", "disruption >75% circumference of D2 (descending or second part) ; involving ampulla or distal common bile duct [OIS IV]", 4, 3, "541020.2"),
    ("541028.5", "massive ; avulsion ; complex ; rupture ; tissue loss ; devascularization ; massive disruption of duodenopancreatic complex [OIS V]", 5, 3, "541020.2"),

    # ── IMAGE 016: Gallbladder / Jejunum-Ileum ────────────────────────────────
    ("541299.2", "Gallbladder NFS",                                                                                    2, 1, None),
    ("541210.2", "contusion ; hematoma [OIS I]",                                                                       2, 2, "541299.2"),
    ("541220.2", "laceration ; perforation NFS [OIS II]",                                                              2, 2, "541299.2"),
    ("541222.2", "minor ; superficial ; no cystic duct involvement [OIS II]",                                          2, 3, "541220.2"),
    ("541224.3", "massive ; avulsion ; complex ; rupture ; tissue loss ; cystic duct laceration or transection [OIS III]", 3, 3, "541220.2"),
    ("541226.4", "with common bile or hepatic duct laceration or transection [OIS IV, V]",                             4, 3, "541220.2"),

    ("541499.2", "Jejunum-Ileum (small bowel) NFS",                                                                    2, 1, None),
    ("541410.2", "contusion ; hematoma [OIS I]",                                                                       2, 2, "541499.2"),
    ("541420.2", "laceration NFS",                                                                                     2, 2, "541499.2"),
    ("541422.2", "no perforation ; partial thickness ; <50% circumference [OIS I, II]",                                2, 3, "541420.2"),
    ("541424.3", "perforation ; full thickness ; ≥50% circumference without transection ; multiple simple wounds [OIS III]", 3, 3, "541420.2"),
    ("541426.4", "massive ; avulsion ; complex ; tissue loss ; transection ; large areas of tissue devitalization or devascularization [OIS IV, V]", 4, 3, "541420.2"),

    # ── IMAGE 018: Kidney ────────────────────────────────────────────────────
    ("541699.2", "Kidney NFS",                                                                                         2, 1, None),
    ("541610.2", "contusion ; hematoma NFS",                                                                           2, 2, "541699.2"),
    ("541612.2", "subcapsular, nonexpanding ; confined to renal retroperitoneum ; minor ; superficial [OIS I, II]",    2, 3, "541610.2"),
    ("541614.3", "subcapsular, >50% surface area or expanding ; major ; large [OIS III]",                              3, 3, "541610.2"),
    ("541620.2", "laceration NFS",                                                                                     2, 2, "541699.2"),
    ("541622.2", "≤1cm parenchymal depth of renal cortex, no urinary extravasation ; minor ; superficial [OIS II]",   2, 3, "541620.2"),
    ("541624.3", ">1cm parenchymal depth of renal cortex, no collecting system rupture or urinary extravasation ; moderate [OIS III]", 3, 3, "541620.2"),
    ("541626.4", "extending through renal cortex, medulla and collecting system ; main renal vessel injury with contained hemorrhage ; major [OIS IV]", 4, 3, "541620.2"),
    ("541628.5", "hilum avulsion ; total destruction of organ and its vascular system [OIS V]",                        5, 3, "541620.2"),
    ("541640.4", "rupture",                                                                                            4, 2, "541699.2"),

    # ── IMAGE 022: Liver / Mesentery ─────────────────────────────────────────
    ("541899.2", "Liver NFS",                                                                                          2, 1, None),
    ("541810.2", "contusion ; hematoma NFS",                                                                           2, 2, "541899.2"),
    ("541812.2", "subcapsular, ≤50% surface area, or nonexpanding ; intraparenchymal ≤10cm in diameter ; minor ; superficial [OIS I, II]", 2, 3, "541810.2"),
    ("541814.3", "subcapsular, >50% surface area or expanding ; ruptured subcapsular or parenchymal ; intraparenchymal >10cm or expanding ; major [OIS III]", 3, 3, "541810.2"),
    ("541820.2", "laceration NFS",                                                                                     2, 2, "541899.2"),
    ("541822.2", "simple capsular tears ; ≤3cm parenchymal depth ; ≤10cm long ; minor ; superficial [OIS II]",         2, 3, "541820.2"),
    ("541824.3", ">3cm parenchymal depth ; major duct involvement ; moderate [OIS III]",                               3, 3, "541820.2"),
    ("541826.4", "parenchymal disruption ≤75% hepatic lobe ; multiple lacerations >3cm deep ; \"burst\" injury ; major [OIS IV]", 4, 3, "541820.2"),
    ("541828.5", "parenchymal disruption of >75% of hepatic lobe or >3 Couinard's segments within a single lobe ; or involving retrohepatic vena cava/central hepatic veins ; massive ; complex [OIS V]", 5, 3, "541820.2"),
    ("541830.6", "hepatic avulsion (total separation of all vascular attachments) [OIS VI]",                           6, 3, "541820.2"),
    ("541840.4", "rupture",                                                                                            4, 2, "541899.2"),

    ("542099.2", "Mesentery NFS",                                                                                      2, 1, None),
    ("542010.2", "contusion ; hematoma",                                                                               2, 2, "542099.2"),
    ("542020.2", "laceration NFS",                                                                                     2, 2, "542099.2"),
    ("542022.2", "minor ; superficial",                                                                                2, 3, "542020.2"),
    ("542024.3", "major",                                                                                              3, 3, "542020.2"),
    ("542026.4", "massive ; avulsion ; complex ; tissue loss",                                                         4, 3, "542020.2"),

    # ── IMAGE 024: Omentum / Ovarian tube / Ovary / Pancreas ─────────────────
    ("542299.2", "Omentum NFS",                                                                                        2, 1, None),
    ("542210.2", "contusion ; hematoma",                                                                               2, 2, "542299.2"),
    ("542220.2", "laceration NFS",                                                                                     2, 2, "542299.2"),
    ("542222.2", "minor ; superficial",                                                                                2, 3, "542220.2"),
    ("542224.3", "major",                                                                                              3, 3, "542220.2"),

    ("542400.2", "Ovarian (Fallopian) tube laceration",                                                                2, 1, None),

    ("542699.1", "Ovary NFS",                                                                                          1, 1, None),
    ("542610.1", "contusion ; hematoma [OIS I]",                                                                       1, 2, "542699.1"),
    ("542620.1", "laceration ; perforation NFS",                                                                       1, 2, "542699.1"),
    ("542622.1", "superficial ; ≤.5cm ; minor [OIS II]",                                                               1, 3, "542620.1"),
    ("542623.2", "deep ; >.5cm [OIS III]",                                                                             2, 3, "542620.1"),
    ("542624.2", "complete parenchymal destruction ; massive ; avulsion ; complex [OIS IV, V]",                        2, 3, "542620.1"),

    ("542899.2", "Pancreas NFS",                                                                                       2, 1, None),
    ("542810.2", "contusion ; hematoma NFS",                                                                           2, 2, "542899.2"),
    ("542812.2", "minor ; superficial ; no duct involvement [OIS I]",                                                  2, 3, "542810.2"),
    ("542814.3", "major ; large ; extensive ; with duct involvement [OIS II]",                                         3, 3, "542810.2"),
    ("542820.2", "laceration NFS",                                                                                     2, 2, "542899.2"),
    ("542822.2", "minor ; superficial ; no duct involvement [OIS I]",                                                  2, 3, "542820.2"),
    ("542824.3", "moderate ; major vessel or major duct involvement ; distal transection [OIS III]",                   3, 3, "542820.2"),
    ("542826.4", "if involving ampulla [OIS IV]",                                                                      4, 3, "542820.2"),
    ("542828.4", "major ; multiple lacerations ; proximal transection [OIS IV]",                                       4, 3, "542820.2"),
    ("542830.4", "if involving ampulla [OIS IV]",                                                                      4, 3, "542820.2"),
    ("542832.5", "massive ; avulsion ; complex ; tissue loss ; massive disruption of pancreatic head [OIS V]",         5, 3, "542820.2"),

    # ── IMAGE 026: Penis / Perineum / Prostate / Rectum ──────────────────────
    ("543099.1", "Penis NFS",                                                                                          1, 1, None),
    ("543010.1", "contusion ; hematoma [OIS I]",                                                                       1, 2, "543099.1"),
    ("543020.1", "laceration ; perforation NFS",                                                                       1, 2, "543099.1"),
    ("543022.1", "minor ; superficial [OIS II, III]",                                                                  1, 3, "543020.1"),
    ("543024.2", "major [OIS IV]",                                                                                     2, 3, "543020.1"),
    ("543026.2", "massive ; amputation ; avulsion ; complex [OIS V]",                                                  2, 3, "543020.1"),

    ("543299.1", "Perineum NFS",                                                                                       1, 1, None),
    ("543210.1", "contusion ; hematoma",                                                                               1, 2, "543299.1"),
    ("543220.1", "laceration ; perforation NFS",                                                                       1, 2, "543299.1"),
    ("543222.1", "minor ; superficial",                                                                                1, 3, "543220.1"),
    ("543224.2", "major",                                                                                              2, 3, "543220.1"),
    ("543226.3", "massive ; avulsion ; complex",                                                                       3, 3, "543220.1"),

    ("543599.1", "Prostate NFS",                                                                                       1, 1, None),
    ("543510.1", "contusion ; hematoma",                                                                               1, 2, "543599.1"),
    ("543520.2", "laceration NFS",                                                                                     2, 2, "543599.1"),
    ("543522.3", "involving urethra",                                                                                  3, 3, "543520.2"),

    ("543699.2", "Rectum NFS",                                                                                         2, 1, None),
    ("543610.2", "contusion ; hematoma [OIS I]",                                                                       2, 2, "543699.2"),
    ("543620.2", "laceration NFS",                                                                                     2, 2, "543699.2"),
    ("543622.2", "no perforation ; partial thickness ; ≤50% of circumference [OIS I, II]",                             2, 3, "543620.2"),
    ("543624.3", "full thickness ; >50% circumference [OIS III]",                                                      3, 3, "543620.2"),
    ("543625.4", "extending into perineum [OIS IV]",                                                                   4, 3, "543620.2"),
    ("543626.5", "massive ; avulsion ; tissue loss ; devascularization [OIS V]",                                       5, 3, "543620.2"),

    # ── IMAGE 028: Retroperitoneum / Scrotum / Spleen ────────────────────────
    ("543800.2", "Retroperitoneum hemorrhage or hematoma",                                                             2, 1, None),

    ("544099.1", "Scrotum NFS",                                                                                        1, 1, None),
    ("544010.1", "contusion ; hematoma [OIS I]",                                                                       1, 2, "544099.1"),
    ("544020.1", "laceration ; perforation NFS",                                                                       1, 2, "544099.1"),
    ("544022.1", "minor ; superficial ; <25% diameter [OIS II]",                                                       1, 3, "544020.1"),
    ("544024.2", "major ; amputation ; avulsion ; complex [OIS III, IV, V]",                                           2, 3, "544020.1"),

    ("544299.2", "Spleen NFS",                                                                                         2, 1, None),
    ("544210.2", "contusion ; hematoma NFS",                                                                           2, 2, "544299.2"),
    ("544212.2", "subcapsular, ≤50% surface area ; intraparenchymal, ≤5cm in diameter ; minor ; superficial [OIS I, II]", 2, 3, "544210.2"),
    ("544214.3", "subcapsular, >50% surface area or expanding ; ruptured subcapsular or parenchymal ; intraparenchymal >5cm in diameter or expanding ; major [OIS III]", 3, 3, "544210.2"),
    ("544220.2", "laceration NFS",                                                                                     2, 2, "544299.2"),
    ("544222.2", "simple capsular tear ≤3cm parenchymal depth and no trabecular vessel involvement ; minor ; superficial [OIS I, II]", 2, 3, "544220.2"),
    ("544224.3", "no hilar or segmental parenchymal disruption or destruction ; >3cm parenchymal depth or involving trabecular vessels ; moderate [OIS III]", 3, 3, "544220.2"),
    ("544226.4", "involving segmental or hilar vessels producing major devascularization of >25% of spleen but no hilar injury ; major [OIS IV]", 4, 3, "544220.2"),
    ("544228.5", "hilar disruption producing total devascularization ; tissue loss ; avulsion ; massive [OIS V]",      5, 3, "544220.2"),
    ("544240.3", "rupture NFS",                                                                                        3, 2, "544299.2"),

    # ── IMAGE 030: Stomach / Testes / Ureter / Urethra ───────────────────────
    ("544499.2", "Stomach NFS",                                                                                        2, 1, None),
    ("544410.2", "contusion ; hematoma [OIS I]",                                                                       2, 2, "544499.2"),
    ("544414.3", "ingestion injury",                                                                                   3, 2, "544499.2"),
    ("544415.3", "partial thickness necrosis",                                                                         3, 3, "544414.3"),
    ("544416.4", "full thickness necrosis",                                                                            4, 3, "544414.3"),
    ("544420.2", "laceration NFS",                                                                                     2, 2, "544499.2"),
    ("544422.2", "no perforation ; partial thickness [OIS I]",                                                         2, 3, "544420.2"),
    ("544424.3", "perforation ; full thickness [OIS II, III]",                                                         3, 3, "544420.2"),
    ("544426.4", "avulsion ; complex ; rupture ; tissue loss ; massive ; devascularization [OIS IV, V]",               4, 3, "544420.2"),

    ("544699.1", "Testes NFS",                                                                                         1, 1, None),
    ("544610.1", "contusion ; hematoma [OIS I]",                                                                       1, 2, "544699.1"),
    ("544620.1", "laceration NFS",                                                                                     1, 2, "544699.1"),
    ("544622.1", "minor ; superficial [OIS II, III]",                                                                  1, 3, "544620.1"),
    ("544624.2", "avulsion ; amputation ; complex ; massive [OIS IV, V]",                                              2, 3, "544620.1"),

    ("544899.2", "Ureter NFS",                                                                                         2, 1, None),
    ("544810.2", "contusion ; hematoma [OIS I]",                                                                       2, 2, "544899.2"),
    ("544820.2", "laceration NFS",                                                                                     2, 2, "544899.2"),
    ("544822.2", "no perforation ; partial thickness [OIS II]",                                                        2, 3, "544820.2"),
    ("544824.3", "perforation ; full thickness [OIS III]",                                                             3, 3, "544820.2"),
    ("544826.3", "massive ; avulsion ; complex ; rupture ; tissue loss ; transection [OIS IV, V]",                     3, 3, "544820.2"),

    ("545099.2", "Urethra NFS",                                                                                        2, 1, None),
    ("545010.2", "contusion ; hematoma [OIS I]",                                                                       2, 2, "545099.2"),
    ("545020.2", "laceration NFS",                                                                                     2, 2, "545099.2"),
    ("545022.2", "no perforation ; partial thickness [OIS III]",                                                       2, 3, "545020.2"),
    ("545024.2", "perforation ; full thickness ; urethral separation <2cm [OIS IV]",                                   2, 3, "545020.2"),
    ("545026.2", "avulsion ; complex ; tissue loss ; massive [OIS IV]",                                                2, 3, "545020.2"),
    ("545028.3", "posterior tissue loss ; transection ; urethral separation >2cm [OIS V]",                             3, 3, "545020.2"),
    ("545030.2", "stretch injury [OIS II]",                                                                            2, 2, "545099.2"),

    # ── IMAGE 032: Uterus / Vagina / Vulva ───────────────────────────────────
    ("545299.1", "Uterus NFS",                                                                                         1, 1, None),
    ("545210.2", "contusion ; hematoma",                                                                               2, 2, "545299.1"),
    ("545220.2", "laceration ; perforation NFS",                                                                       2, 2, "545299.1"),
    ("545222.2", "≤1cm ; minor ; superficial [OIS II]",                                                                2, 3, "545220.2"),
    ("545224.3", ">1cm ; placental abruption ≤50% ; major ; deep [OIS III]",                                          3, 3, "545220.2"),
    ("545226.4", "involving uterine artery ; placental abruption >50% but not complete [OIS IV]",                      4, 3, "545220.2"),
    ("545228.5", "uterine rupture ; avulsion ; devascularization ; complete placental abruption",                      5, 3, "545220.2"),

    ("545499.1", "Vagina NFS",                                                                                         1, 1, None),
    ("545410.1", "contusion ; hematoma [OIS I]",                                                                       1, 2, "545499.1"),
    ("545420.1", "laceration ; perforation NFS",                                                                       1, 2, "545499.1"),
    ("545422.1", "minor ; superficial ; mucosa only [OIS II]",                                                         1, 3, "545420.1"),
    ("545424.2", "major ; deep into fat/muscle [OIS III]",                                                             2, 3, "545420.1"),
    ("545426.3", "massive ; avulsion ; complex ; into cervix or peritoneum [OIS IV, V]",                               3, 3, "545420.1"),

    ("545699.1", "Vulva NFS",                                                                                          1, 1, None),
    ("545610.1", "contusion ; hematoma",                                                                               1, 2, "545699.1"),
    ("545620.1", "laceration ; perforation NFS",                                                                       1, 2, "545699.1"),
    ("545622.1", "minor ; superficial ; skin only [OIS II]",                                                           1, 3, "545620.1"),
    ("545624.2", "major ; deep into fat/muscle [OIS III]",                                                             2, 3, "545620.1"),
    ("545626.3", "massive ; avulsion ; complex [OIS IV, V]",                                                           3, 3, "545620.1"),
]


def infer_injury_types(description: str) -> list:
    desc_lower = description.lower()
    types_found = []
    injury_keywords = {
        "nfs": ["nfs"],
        "abrasion": ["abrasion"],
        "contusion": ["contusion"],
        "hematoma": ["hematoma"],
        "laceration": ["laceration"],
        "avulsion": ["avulsion", "degloving"],
        "rupture": ["rupture"],
        "perforation": ["perforation", "puncture"],
        "hemorrhage": ["hemorrhage", "blood loss", "hemoperitoneum"],
        "amputation": ["amputation"],
        "penetrating": ["penetrating"],
        "transection": ["transection"],
        "thrombosis": ["thrombosis"],
        "devascularization": ["devascularization", "devascularized"],
        "bilateral": ["bilateral"],
        "intimal_tear": ["intimal tear"],
        "necrosis": ["necrosis"],
        "stretch": ["stretch"],
        "crush": ["crush"],
    }
    for type_name, keywords in injury_keywords.items():
        if any(kw in desc_lower for kw in keywords):
            types_found.append(type_name)
    return types_found if types_found else ["other"]


def build_explanation(code: str, code_to_entry: dict) -> tuple[str, str]:
    """Build explanation path (parent breadcrumb) for an entry."""
    entry = code_to_entry.get(code, {})
    parent_code = entry.get("parent_code")
    if not parent_code:
        return "", ""

    path_ja = []
    path_en = []
    current = parent_code
    visited = set()

    while current and current not in visited:
        visited.add(current)
        p = code_to_entry.get(current, {})
        if p:
            if p.get("japanese"):
                path_ja.insert(0, p["japanese"])
            if p.get("english"):
                path_en.insert(0, p["english"])
        current = p.get("parent_code")

    return " > ".join(path_ja), " > ".join(path_en)


def main():
    with open(JP_LOOKUP_PATH, encoding="utf-8") as f:
        jp_lookup = json.load(f)

    # Build code → entry map first pass (without explanations)
    code_to_entry = {}
    entries = []
    for (code, english, severity, level, parent) in RAW_ENTRIES:
        jp = jp_lookup.get(code, {}).get("japanese", "")
        existing_types = jp_lookup.get(code, {}).get("injury_types", [])
        injury_types = existing_types if existing_types else infer_injury_types(english)

        entry = {
            "code": code,
            "japanese": jp,
            "english": english,
            "ais_severity": severity,
            "hierarchy_level": level,
            "section": "ABDOMEN",
            "parent_code": parent,
            "explanation_ja": "",
            "explanation_en": "",
            "injury_types": injury_types,
            "iss_body_region": "abdomen",
        }
        entries.append(entry)
        code_to_entry[code] = entry

    # Second pass: fill explanation fields
    for entry in entries:
        expl_ja, expl_en = build_explanation(entry["code"], code_to_entry)
        entry["explanation_ja"] = expl_ja
        entry["explanation_en"] = expl_en

    output = {
        "body_part": "abdomen",
        "source": "abdomen_images",
        "total_entries": len(entries),
        "entries": entries,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Generated {len(entries)} entries -> {OUTPUT_PATH}")
    by_level = {}
    for e in entries:
        lvl = e["hierarchy_level"]
        by_level[lvl] = by_level.get(lvl, 0) + 1
    for lvl in sorted(by_level):
        print(f"  Level {lvl}: {by_level[lvl]} entries")

    missing_jp = [e["code"] for e in entries if not e["japanese"]]
    if missing_jp:
        print(f"\nMissing Japanese ({len(missing_jp)} codes):")
        for c in missing_jp:
            print(f"  {c}")


if __name__ == "__main__":
    main()
