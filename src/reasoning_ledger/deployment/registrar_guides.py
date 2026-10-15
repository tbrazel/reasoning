"""Curated browser handoffs; no registrar API access or DNS migration writes."""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path

from . import setup_ui
from .dns import NAMESERVERS, zone_name
from .host import InstallError
from .private import read_document, write_document
from .setup_auth import open_page

CATALOG_URL = "https://docs.digitalocean.com/products/networking/dns/getting-started/dns-registrars/"
DNSSEC_URL = "https://docs.digitalocean.com/support/why-does-my-domain-fail-to-resolve/"
CLOUDFLARE_HELP = "https://developers.cloudflare.com/dns/manage-dns-records/how-to/subdomains-outside-cloudflare/"


@dataclass(frozen=True)
class RegistrarGuide:
    id: str
    name: str
    dashboard: str
    help: str
    steps: tuple[str, ...]


GUIDES = (
    RegistrarGuide("bluehost", "Bluehost", "https://www.bluehost.com/my-account/login",
                   "https://www.bluehost.com/help/article/custom-nameservers", (
                       "Domains > select your domain > Nameservers > Change Nameservers.",
                       "Replace the current entries with all three DigitalOcean nameservers below, then save.",)),
    RegistrarGuide("domain.com", "Domain.com", "https://www.domain.com/",
                   "https://www.networksolutions.com/help/article/ns-what-is-dns", (
                       "Sign in from Domain.com; use the official guide's branch for your account's login page.",
                       "For the Network Solutions account manager: Domains > your domain > Advanced Tools > Nameservers (DNS) > Manage.",
                       "Enter all three below and save; other account layouts follow the guide's linked instructions.",)),
    RegistrarGuide("dreamhost", "DreamHost", "https://panel.dreamhost.com/",
                   "https://help.dreamhost.com/hc/en-us/articles/360038897151-Changing-your-nameservers-at-DreamHost", (
                       "Manage Domains > Manage beside your domain > Nameservers > edit.",
                       "Choose Use another host's nameservers; enter all three below and Confirm.",)),
    RegistrarGuide("dynadot", "Dynadot", "https://www.dynadot.com/account/",
                   "https://www.dynadot.com/help/question/set-name-servers", (
                       "My Domains > Manage Domains > select only your domain > Action > DNS Settings.",
                       "Choose Name Servers > Enter new or existing Name Servers; enter all three below and Save Name Server.",)),
    RegistrarGuide("enom", "Enom", "https://www.enom.com/",
                   "https://support.enom.com/support/solutions/articles/201000065375", (
                       "Domains > My domains > click your domain name > DNS server settings.",
                       "Choose Custom, enter all three DigitalOcean nameservers below, and save the change.",)),
    RegistrarGuide("gandi", "Gandi", "https://admin.gandi.net/",
                   "https://docs.gandi.net/en/domain_names/common_operations/changing_nameservers.html", (
                       "Domain > your domain > Nameservers > Change > External.",
                       "Enter all three DigitalOcean nameservers below and Save.",)),
    RegistrarGuide("godaddy", "GoDaddy", "https://dcc.godaddy.com/",
                   "https://www.godaddy.com/en/help/change-my-domain-nameservers-664", (
                       "Domain Portfolio > your domain > DNS > Nameservers > I'll use my own nameservers.",
                       "Enter all three below, Save, then Continue; complete identity verification if requested.",)),
    RegistrarGuide("hostgator", "HostGator", "https://portal.hostgator.com/",
                   "https://www.hostgator.com/help/article/what-are-my-name-servers", (
                       "Customer Portal > Domains > your domain > Nameservers > Change Nameservers.",
                       "Enter all three DigitalOcean nameservers below (not HostGator's example servers), then Save.",)),
    RegistrarGuide("hover", "Hover", "https://www.hover.com/control_panel",
                   "https://support.hover.com/support/solutions/articles/201000064742", (
                       "Select your domain > Overview > Nameservers > Edit.",
                       "Replace the current entries, add a field for the third nameserver if needed, then Save nameservers.",)),
    RegistrarGuide("ionos", "IONOS", "https://my.ionos.com/domains",
                   "https://www.ionos.com/help/domains/using-your-own-name-servers/using-your-own-name-servers-for-a-domain/", (
                       "Domains > gear under Actions beside your domain > Name Server > Use Custom Name Servers.",
                       "Enter the three below in Name Server 1, 2, and 3; leave unused fields empty and Save.",)),
    RegistrarGuide("name.com", "Name.com", "https://www.name.com/account/domain",
                   "https://www.name.com/support/articles/205934547-changing-nameservers-for-dns-management", (
                       "My Domains > your domain > Domain Actions > Manage Nameservers.",
                       "Delete All current nameservers; enter each of the three below via Add Nameserver > Add, then Save Changes.",)),
    RegistrarGuide("namecheap", "Namecheap", "https://ap.www.namecheap.com/domains/list/",
                   "https://www.namecheap.com/support/knowledgebase/article.aspx/767/10/how-to-change-dns-for-a-domain/", (
                       "Domain List > Manage beside your domain > Nameservers > CustomDNS.",
                       "Enter all three DigitalOcean nameservers below; click the green checkmark to save.",)),
    RegistrarGuide("porkbun", "Porkbun", "https://porkbun.com/account/domainsSpeedy",
                   "https://kb.porkbun.com/article/22-how-to-change-nameservers", (
                       "Domain Management > Details beside your domain > edit Nameservers.",
                       "Remove the old entries and paste all three below, one per line; Save Nameservers and Submit if prompted.",)),
    RegistrarGuide("squarespace", "Squarespace", "https://account.squarespace.com/domains",
                   "https://support.squarespace.com/hc/en-us/articles/4404183898125-Review-change-or-reset-your-domain-s-nameservers", (
                       "Domains dashboard > your domain > DNS > Domain Nameservers > Use Custom Nameservers.",
                       "Reauthenticate if requested; handle DNSSEC as warned above, enter all three (add a third field), then Save.",)),
)

CLOUDFLARE = RegistrarGuide("cloudflare", "Cloudflare DNS", "https://dash.cloudflare.com/", CLOUDFLARE_HELP, (
    "Cloudflare currently hosts this zone's DNS. First determine whether the selected zone is the registered domain "
    "or a dedicated subdomain.",
    "If Cloudflare Registrar holds the registered domain, its apex cannot use DigitalOcean nameservers. Stop and start "
    "a fresh setup with a dedicated subdomain instead; transferring the registration is not required for that option.",
    "For a dedicated subdomain: select the parent domain in Cloudflare > DNS > Records, then add three NS records at "
    "the subdomain label, one for each DigitalOcean nameserver below.",
    "Before delegation, reproduce any records at or below that subdomain in DigitalOcean. Remove conflicting A, AAAA, "
    "or CNAME records at the delegation label; keep the parent domain and its other records on Cloudflare.",
))

MANUAL = RegistrarGuide("manual", "Other / manual", "", CATALOG_URL, (
    "For a delegated subdomain, keep the registered domain's nameservers and add NS records "
    "for this exact subdomain at its parent DNS provider.",
    "For a registered domain, open its registrar nameserver settings and replace the old entries with all three below.",
    "If the relevant provider cannot publish the delegation, stop and ask its support; do not transfer or buy a domain automatically.",
))
BY_ID = {guide.id: guide for guide in (*GUIDES, CLOUDFLARE, MANUAL)}


def detected_dns_guide(nameservers) -> RegistrarGuide | None:
    if (
        isinstance(nameservers, list)
        and 1 <= len(nameservers) <= 10
        and all(isinstance(value, str) and value.endswith(".ns.cloudflare.com") for value in nameservers)
    ):
        return CLOUDFLARE
    return None


def confirm_detected_guide(detected: RegistrarGuide) -> bool:
    while True:
        answer = input(f"Detected {detected.name}. Use its delegation instructions? [Y/n]: ").strip().casefold()
        if answer in {"", "y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Enter yes or no.")


def choose_guide(detected: RegistrarGuide | None = None):
    """Stable numbers or case-insensitive names; ambiguous searches never select silently."""
    if detected is not None and confirm_detected_guide(detected):
        return detected
    matches = list(GUIDES)
    while True:
        setup_ui.registrar_choices(GUIDES, matches)
        answer = input("Registrar: ").strip().casefold()
        if answer in {"0", "other", "manual"}:
            return MANUAL
        if len(answer) <= 2 and answer.isascii() and answer.isdecimal() and 1 <= int(answer) <= len(GUIDES):
            return GUIDES[int(answer) - 1]
        exact = [guide for guide in GUIDES if answer in {guide.id, guide.name.casefold()}]
        if exact:
            return exact[0]
        matches = [guide for guide in GUIDES if answer and answer in guide.name.casefold()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            print("No matching registrar. Choose 0 for manual instructions, or try another search.")
            matches = list(GUIDES)
        else:
            print("Several registrars match; choose one of the numbered results.")


def saved_guide(directory: Path, config: dict, observed_nameservers=None, *, parent_zone=None, confirmed_dns_provider=None):
    binding = {"format": 1, "release_id": config["release_id"], "zone": zone_name(config["zone"])}
    path = directory / "registrar-guide.json"
    if (path.exists() or path.is_symlink()) and parent_zone is not None:
        previous = read_document(path)
        if (
            isinstance(previous, dict)
            and set(previous) == {"format", "release_id", "zone", "registrar"}
            and type(previous.get("format")) is int
            and previous["format"] == 1
            and previous.get("release_id") == binding["release_id"]
            and previous.get("zone") == parent_zone
            and previous.get("registrar") in BY_ID
            and binding["zone"].endswith("." + parent_zone)
        ):
            suffix = hashlib.sha256(binding["zone"].encode("ascii")).hexdigest()[:12]
            path = directory / ("registrar-guide-" + suffix + ".json")
    detected = detected_dns_guide(observed_nameservers)
    if path.exists() or path.is_symlink():
        value = read_document(path)
        if (set(value) != {*binding, "registrar"} or type(value["format"]) is not int
                or any(value[key] != expected for key, expected in binding.items())
                or not isinstance(value["registrar"], str) or value["registrar"] not in BY_ID):
            raise InstallError("saved registrar guide does not match this release and zone; preserve setup state")
        guide = BY_ID[value["registrar"]]
        if guide is MANUAL and detected is not None and confirm_detected_guide(detected):
            value["registrar"] = detected.id
            write_document(path, value)
            return detected
        return guide
    guide = (
        CLOUDFLARE
        if confirmed_dns_provider == "cloudflare" and detected is CLOUDFLARE
        else choose_guide(detected)
    )
    write_document(path, {**binding, "registrar": guide.id}, new=True)
    return guide


def guide_delegation(
    directory: Path,
    config: dict,
    observed_nameservers=None,
    *,
    parent_zone=None,
    confirmed_dns_provider=None,
):
    guide = saved_guide(
        directory,
        config,
        observed_nameservers,
        parent_zone=parent_zone,
        confirmed_dns_provider=confirmed_dns_provider,
    )
    steps = guide.steps
    if guide is CLOUDFLARE and parent_zone is not None:
        label = config["zone"][: -(len(parent_zone) + 1)]
        steps = (
            f"Open Cloudflare, select {parent_zone}, then open DNS > Records.",
            f"Add the three NS records shown below. Use {label} as the Name for each one.",
            f"Do not change {parent_zone}'s nameservers. Leave all unrelated Cloudflare records alone.",
        )
    setup_ui.delegation_guide(
        config["zone"],
        guide.name,
        steps,
        NAMESERVERS,
        dashboard=guide.dashboard,
        help_url=guide.help,
        dnssec_url=DNSSEC_URL,
        parent_zone=parent_zone,
    )
    browser_prompt = "Open the official guide" + (" and registrar dashboard" if guide.dashboard else "") + " in your browser? [Y/n]: "
    while True:
        answer = input(browser_prompt).strip().casefold()
        if answer in {"", "y", "yes", "n", "no"}:
            break
        print("Enter yes or no; links are also printed for remote/headless terminals.")
    if answer not in {"n", "no"}:
        if guide.dashboard:
            open_page(guide.dashboard)
        open_page(guide.help)
    return guide.id


def resume_command(directory: Path, bundle: Path, compute_backend: str) -> str:
    arguments = ["uv", "run"]
    if compute_backend == "modal":
        arguments.extend(["--extra", "modal"])
    arguments.extend(["reasoning-deploy", "setup", "--bundle", str(bundle.absolute()), "--state-dir", str(directory.absolute())])
    if compute_backend == "modal":
        arguments.extend(["--compute-backend", "modal"])
    return shlex.join(arguments)
