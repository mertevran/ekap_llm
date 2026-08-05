import json
from pathlib import Path


def update_rules():
    rules_dir = Path("config/isbak/evaluation_rules")
    for file_path in rules_dir.glob("*.json"):
        with open(file_path, encoding="utf-8") as f:
            data = json.loads(f.read())

        data["surum"] = "1.1.0"

        # 1. zorunlu_kapi_kurallari
        if "zorunlu_kapi_kurallari" in data:
            for rule in data["zorunlu_kapi_kurallari"]:
                davranis = rule.get("davranis", "")
                davranis = davranis.replace("uygun_degil", "ilgisiz")
                davranis = davranis.replace("kosullu_uygun", "inceleme_gerekli")
                # Avoid accidentally turning "uygun" into "doğrudan_doğrudan_uygun" if not careful.
                # Actually, in the texts it's usually "uygun_degil". I've replaced that.
                # Let's replace standalone " uygun;" or similar.
                davranis = davranis.replace(" uygun;", " doğrudan_uygun;")
                davranis = davranis.replace(" uygun.", " doğrudan_uygun.")
                rule["davranis"] = davranis

        # 2. karar_esikleri
        if "karar_esikleri" in data:
            old_esikler = data["karar_esikleri"]
            new_esikler = {}

            if "uygun" in old_esikler:
                new_esikler["doğrudan_uygun"] = old_esikler["uygun"]
            elif "doğrudan_uygun" in old_esikler:
                new_esikler["doğrudan_uygun"] = old_esikler["doğrudan_uygun"]

            if "uygun_degil" in old_esikler:
                new_esikler["ilgisiz"] = old_esikler["uygun_degil"]
            elif "ilgisiz" in old_esikler:
                new_esikler["ilgisiz"] = old_esikler["ilgisiz"]

            # Merge kosullu_uygun and inceleme_gerekli
            inceleme_data = {}
            if "inceleme_gerekli" in old_esikler:
                inceleme_data.update(old_esikler["inceleme_gerekli"])

            if "kosullu_uygun" in old_esikler:
                for k, v in old_esikler["kosullu_uygun"].items():
                    inceleme_data[k] = v

            new_esikler["inceleme_gerekli"] = inceleme_data

            data["karar_esikleri"] = new_esikler

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    update_rules()
    print("All evaluation rules updated successfully.")
