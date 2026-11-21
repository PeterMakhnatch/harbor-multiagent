import json
import re
from typing import TypedDict, TypeGuard

KNOWN_NAMES = frozenset(
    """
    James Michael Robert John David William Richard Joseph Thomas Christopher
    Charles Daniel Matthew Anthony Mark Donald Steven Andrew Paul Joshua
    Kenneth Kevin Brian Timothy Ronald George Jason Edward Jeffrey Ryan
    Jacob Nicholas Gary Eric Jonathan Stephen Larry Justin Scott Brandon
    Benjamin Samuel Gregory Alexander Patrick Frank Raymond Jack Dennis Jerry
    Tyler Aaron Jose Adam Nathan Henry Zachary Douglas Peter Kyle
    Noah Ethan Jeremy Christian Walter Keith Austin Roger Terry Sean
    Gerald Carl Dylan Harold Jordan Jesse Bryan Lawrence Arthur Gabriel
    Bruce Logan Billy Joe Alan Juan Elijah Willie Albert Wayne
    Randy Mason Vincent Liam Roy Bobby Caleb Bradley Russell Lucas
    Mary Patricia Jennifer Linda Elizabeth Barbara Susan Jessica Karen Sarah
    Lisa Nancy Sandra Betty Ashley Emily Kimberly Margaret Donna Michelle
    Carol Amanda Melissa Deborah Stephanie Rebecca Sharon Laura Cynthia Dorothy
    Amy Kathleen Angela Shirley Emma Brenda Pamela Nicole Anna Samantha
    Katherine Christine Debra Rachel Carolyn Janet Maria Olivia Heather Helen
    Catherine Diane Julie Victoria Joyce Lauren Kelly Christina Ruth Joan
    Virginia Judith Evelyn Hannah Andrea Megan Cheryl Jacqueline Madison Teresa
    Abigail Sophia Martha Sara Gloria Janice Kathryn Ann Isabella Judy
    Charlotte Julia Grace Amber Alice Jean Denise Frances Danielle Marilyn
    Natalie Beverly Diana Brittany Theresa Kayla Alexis Doris Lori Tiffany
    """.split()
)
COLORS = ("Group 1", "Group 2", "Group 3", "Group 4")
JSON_RETRY = "Your messages could not be parsed into JSON. Please check your response and try again."
ANSWER_RETRY = (
    "Your answer could not be parsed. Please check your answer and try again."
)
COT_MESSAGES = "Elaborate your chain of thought step-by-step first, then output the messages for your neighbors."
COT_FINAL = "\n\nElaborate your chain of thought step-by-step first, then answer the following: "
ANSWER_PATTERN = re.compile(r"### Final Answer ###\s*(Group 1|Group 2|Group 3|Group 4)")
OPEN_BRACE = re.compile(r"\{")
SYSTEM = """You are an agent that is connected with other agents (your neighbors), who you communicate with. Your neighbors can in turn communicate with their neighbors and so forth. Your task is to partition yourselves into groups such that agents who are neighbors are never in the same group.
The rules are as follows:
1. There are {num_agents} agents in total. Everybody has a unique name. Your name is {own_name}.
2. You can only communicate with your immediate neighbors ({neighbors}). You cannot see or directly communicate with anyone else, unless information is relayed by intermediate agents.
3. You can exchange text-based messages with your neighbors in rounds. In each round, you will first receive the last messages sent by your neighbors and then be asked to generate your response messages which your neighbors receive in the next round. This process repeats for {num_rounds} rounds of message passing. Importantly, the process is synchronous: Every agent decides on which messages to send at the same time and sees the messages from other agents only in the next round.
4. Everybody (including you) decides what to share or request from neighbors. In every round, think step-by-step about the next set of messages you want to send. Output a JSON string that contains your response messages.
5. The messages you send to your neighbors are formatted as JSON. For example, if your neighbors are Alan and Bob, your output should look as follows:\x20
```
{{
    "Alan": "Message that will be sent to Alan.",
    "Bob": "Message that will be sent to Bob.",
}}
```
It is not mandatory to send a message to every neighbor in every round. If you do not want to send a message to a particular neighbor, you may omit their name from the JSON.
6. After {num_rounds} message passes, you have to solve the following task: You will be requested to state which group you assign yourself to. There are exactly {num_colors} groups available: Group 1,...,Group {num_colors}. You should assign yourself to exactly one of these groups. The final result should be such that any two agents who are neighbors are in different groups. In particular, you should assign yourself to a group that is different from all of your neighbors' groups.\x20"""


class ReceivedMessage(TypedDict):
    name: str
    message: str | None


class Observation(TypedDict):
    seat: str
    name: str
    neighbors: list[str]
    num_agents: int
    num_rounds: int
    num_colors: int
    round: int
    phase: str
    submitted: bool
    received: list[ReceivedMessage]


def system_prompt(observation: Observation) -> str:
    return SYSTEM.format(
        num_agents=observation["num_agents"],
        own_name=observation["name"],
        neighbors=", ".join(observation["neighbors"]),
        num_rounds=observation["num_rounds"],
        num_colors=observation["num_colors"],
    )


def user_prompt(observation: Observation, *, chain_of_thought: bool = True) -> str:
    cot = COT_MESSAGES if chain_of_thought else ""
    if observation["round"] == 1 and observation["phase"] == "messages":
        return (
            f"What are the first messages you want to send to your neighbors? {cot} "
            "Output your messages in JSON format as specified earlier."
        )

    final = observation["phase"] == "final"
    if final:
        header = "Message passing has finished, here are the last messages you got from your neighbors:\n\n"
    elif observation["phase"] == "messages":
        header = "These are the messages from your neighbors:\n\n"
    else:
        raise ValueError("A terminal observation has no user prompt")

    parts = [header]
    silent = []
    for entry in observation["received"]:
        name, message = entry["name"], entry["message"]
        if message is None:
            silent.append(name)
        else:
            parts.append(f"Message from {name}:\n\n{message}\n\n")
    if silent:
        parts.append(
            "The following neighbors did not send you a messages in this round: "
            f"{', '.join(silent)}\n\n"
        )

    if final:
        if chain_of_thought:
            parts.append(COT_FINAL)
        parts.append(
            "Which group do you assign yourself to? Format your answer as follows: "
            "'### Final Answer ###', followed by your final answer. Don't use any text for "
            f"your final answer except one of these valid options: {', '.join(COLORS)}"
        )
    else:
        rounds_left = observation["num_rounds"] - observation["round"] + 1
        neighbors = ", ".join(observation["neighbors"])
        parts.append(
            f"{cot} Output your messages in JSON format as specified earlier. You have "
            f"{rounds_left} rounds of communication left before you need to decide. "
            f"Your neighbors are: {neighbors} "
        )
        if rounds_left == 1:
            parts.append(
                "These are the last messages that your neighbors will receive from you."
            )
    return "".join(parts)


def is_message_map(value: object) -> TypeGuard[dict[str, str]]:
    return isinstance(value, dict) and all(
        isinstance(name, str) and name in KNOWN_NAMES and isinstance(message, str)
        for name, message in value.items()
    )


def parse_messages(text: str) -> dict[str, str] | None:
    text = text.replace("\\n", "\n")
    end = text.rfind("}")
    if end < 0:
        return None
    for match in OPEN_BRACE.finditer(text, 0, end):
        try:
            candidate = json.loads(text[match.start() : end + 1])
        except (ValueError, RecursionError):
            continue
        if is_message_map(candidate):
            return candidate
    return None


def parse_answer(text: str) -> str | None:
    match = ANSWER_PATTERN.search(text)
    return None if match is None else match.group(1)


def score_coloring(
    answers: list[object], edges: tuple[tuple[int, int], ...]
) -> dict[str, float]:
    edge_fraction = 0.0
    if len(answers) == len(COLORS) and all(answer in COLORS for answer in answers):
        edge_fraction = sum(answers[a] != answers[b] for a, b in edges) / len(edges)
    return {
        "edge_fraction": edge_fraction,
        "proper_coloring": float(edge_fraction == 1.0),
    }
