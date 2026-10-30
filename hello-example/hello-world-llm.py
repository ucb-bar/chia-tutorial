
from chia.base.ChiaFunction import ChiaFunction, get
from chia.models.antigravity import AntigravityLLM, AntigravityQueryResult

@ChiaFunction()
def print_hello_world():
    print("Hello World (#1) from a remote call!")

def main():
    get(print_hello_world.chia_remote())

    llm = AntigravityLLM()
    resp : AntigravityQueryResult = get(llm.prompt.chia_remote(llm, "Briefly say hello to the CHIA tutorial participants"))
    print("LLM Responded:")
    print(resp.result)

if __name__ == "__main__":
    main()
