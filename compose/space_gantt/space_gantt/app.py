import re
import os
import requests
import pickle
import logging
from flask import Flask, jsonify
from pathlib import Path
from requests.auth import HTTPBasicAuth
from typing import Optional, List, Iterator, Any, Tuple, Sequence
from datetime import datetime, timedelta, date
from pydantic import BaseModel
from concurrent.futures import ProcessPoolExecutor

app = Flask(__name__)

SPACE_TAG_IDS = {"gantt": "40CGDl33CsAM"}
SPACE_TOKEN = os.environ.get("SPACE_TOKEN", "")

OPENPROJECT_PROJECT_ID = 3
OPENPROJECT_KEY = os.environ.get("OPENPROJECT_KEY", "")

HERE = Path(__file__).parent.resolve()
ITEMS_SAVED = HERE / "space_items.pickle"

EXECUTOR = ProcessPoolExecutor
executor = EXECUTOR()

logger = logging.getLogger(__name__)

handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s: %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)


class SpaceAPI:
    def __init__(self, token: str = SPACE_TOKEN):
        self.token = token
        self.base = "https://discoball.jetbrains.space"
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.project_key = "APP"
        self.tag = SPACE_TAG_IDS["gantt"]

    def request(
        self, method: str, *args: Any, **kwargs: Any
    ) -> Iterator[dict[str, Any]]:
        headers = kwargs.setdefault("headers", {})
        headers.update(self.headers)
        params = kwargs.setdefault("params", {})
        http_response = getattr(requests, method)(*args, **kwargs)
        http_response.raise_for_status()
        response = http_response.json()
        if not response.get("data"):
            yield response
            return
        yield from response["data"]
        if int(response["next"]) < response["totalCount"]:
            params["$skip"] = response["next"]
            yield from self.request(method, *args, **kwargs)

    def get_all(self) -> Iterator[dict[str, Any]]:
        yield from self.request(
            "get",
            f"{self.base}/api/http/projects/key:{self.project_key}/planning/issues",
            params={"tags": self.tag, "sorting": "CREATED", "descending": "true"},
        )

    def get_item(self, id: int) -> dict[str, Any]:
        return next(
            self.request(
                "get",
                f"{self.base}/api/http/projects/key:{self.project_key}"
                f"/planning/issues/number:{id}",
                params={"$fields": "description,tags"},
            )
        )


class OpenProjectAPI:
    def __init__(self, key: str = OPENPROJECT_KEY):
        self.key = key
        self.base = "https://openproject.discoball.life"
        self.project_id = OPENPROJECT_PROJECT_ID

    def request(
        self, method: str, *args: Any, **kwargs: Any
    ) -> Iterator[dict[str, Any]]:
        params = kwargs.setdefault("params", {})
        page = params.get("offset", 1)
        kwargs["auth"] = HTTPBasicAuth("apikey", self.key)
        headers = kwargs.setdefault("headers", {})
        headers.setdefault("Content-Type", "application/json")
        http_response = getattr(requests, method)(*args, **kwargs)
        http_response.raise_for_status()
        if http_response.status_code == 204:
            return
        response = http_response.json()
        if "total" not in response:
            yield response
            return
        elements = response["_embedded"]["elements"]
        if not elements:
            return
        yield from elements
        if response["count"] == response["total"]:
            return
        params["offset"] = page + 1
        yield from self.request(method, *args, **kwargs)

    def get_all(self) -> Iterator[dict[str, Any]]:
        yield from self.request(
            "get", f"{self.base}/api/v3/projects/{self.project_id}/work_packages"
        )

    def get_relations(self, id: int) -> Iterator[dict[str, Any]]:
        yield from self.request(
            "get", f"{self.base}/api/v3/work_packages/{id}/relations"
        )

    def create_work_package(self, **kwargs: Any) -> dict[str, Any]:
        kwargs["_links"] = {"project": {"href": f"/api/v3/projects/{self.project_id}"}}
        return next(
            self.request("post", f"{self.base}/api/v3/work_packages", json=kwargs)
        )

    def create_relation(
        self, from_item: int, to_item: int, **kwargs: Any
    ) -> dict[str, Any]:
        j = kwargs.setdefault("json", {})
        links = j.setdefault("_links", {})
        links.update(
            {
                "from": {"href": f"/api/v3/work_packages/{from_item}"},
                "to": {"href": f"/api/v3/work_packages/{to_item}"},
            }
        )
        return next(
            self.request(
                "post",
                f"{self.base}/api/v3/work_packages/{from_item}/relations",
                **kwargs,
            )
        )

    def clear_work_packages(self) -> None:
        for item in list(self.get_all()):
            list(
                self.request("delete", f"{self.base}/api/v3/work_packages/{item['id']}")
            )


class SpaceItem(BaseModel):
    id: int
    title: str
    description: str = ""
    earliest_start: date
    due_date: Optional[datetime] = None
    tags: List[str] = []
    dependent_on: List[int] = []
    duration: int = 1


class OpenProjectItem(BaseModel):
    id: int
    subject: str
    start_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    dependent_on: List[int] = []


def get_from_space(api: SpaceAPI) -> Iterator[SpaceItem]:
    for obj in api.get_all():
        id = obj["number"]
        extra = api.get_item(id)
        description = extra["description"] or ""
        dependencies, duration, earliest_start = process_space_item_description(
            description
        )
        if earliest_start is None:
            earliest_start = datetime.fromisoformat(
                obj["creationTime"]["iso"][:-1]
            ).date()
        due_date = datetime.fromisoformat(d["iso"]) if (d := obj["dueDate"]) else None
        tags = [tag["name"] for tag in extra["tags"]]
        yield SpaceItem(
            id=id,
            title=obj["title"],
            description=description,
            earliest_start=earliest_start,
            due_date=due_date,
            tags=tags,
            dependent_on=dependencies,
            duration=duration,
        )


def process_space_item_description(
    description: str,
) -> Tuple[List[int], int, Optional[date]]:
    d = description.strip().splitlines()
    deps = []
    duration = 1
    earliest = None
    for line in d:
        cleaned = line.strip().lower()
        if not cleaned:
            continue
        if cleaned.startswith("---"):
            break
        if m := re.match(r"^.*depends.*https://.*/(\d+)$", cleaned):
            deps.append(int(m.group(1)))
        if m := re.match(r".*duration.* (\d+).*d.*", cleaned):
            duration = int(m.group(1))
        if m := re.match(r".*earliest.*(\d+/\d+/\d+).*", cleaned):
            earliest = datetime.strptime(m.group(1), "%m/%d/%Y").date()
    return (deps, duration, earliest)


def space_to_openproject(space: SpaceAPI, openproject: OpenProjectAPI) -> None:
    logger.info("Starting Space -> OpenProject sync...")
    space_items = list(get_from_space(space))
    if not updated(space_items):
        logger.info("Items up-to-date. Exiting.")
        return

    logger.info("Items changed in Space")
    logger.info("Clearing up old items in OpenProject")
    openproject.clear_work_packages()
    logger.info("OpenProject is now empty")
    translation_map = {}
    logger.info("Processing %s items", len(space_items))
    for item in space_items:
        start = item.earliest_start
        logger.info("..Processing item '%s'", item.title)
        wp = openproject.create_work_package(
            subject=f"[{item.id}] {item.title}",
            startDate=start.isoformat(),
            dueDate=(start + timedelta(days=item.duration)).isoformat(),
        )
        translation_map[item.id] = wp["id"]
    logger.info("Creating relations")
    for item in space_items:
        orig = translation_map[item.id]
        for dep in item.dependent_on:
            target = translation_map[dep]
            logger.info("..Connecting %s -> %s", orig, target)
            openproject.create_relation(
                orig, target, json={"type": "follows", "reverseType": "precedes"}
            )
    logger.info("Saving state")
    save(space_items)
    logger.info("All done")


def updated(items: Sequence[SpaceItem]) -> bool:
    ITEMS_SAVED.touch()
    try:
        previous = pickle.loads(ITEMS_SAVED.read_bytes())
    except Exception:
        previous = []
    return items != previous


def save(items: Sequence[SpaceItem]) -> None:
    ITEMS_SAVED.write_bytes(pickle.dumps(items))


def main() -> None:
    space = SpaceAPI()
    openproject = OpenProjectAPI()
    space_to_openproject(space, openproject)


@app.route("/", methods=["POST"])
def serve() -> str:
    executor.submit(main)
    return jsonify({})
