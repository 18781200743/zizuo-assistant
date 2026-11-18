import sys


def _run() -> None:
    # 打包成 exe 后：子进程用同一 exe + --game-window 打开游戏窗
    if "--game-window" in sys.argv:
        from app.game_window import main as game_main

        # 去掉标记，留给 argparse
        sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--game-window"]
        game_main()
        return

    from app.ui import main

    main()


if __name__ == "__main__":
    _run()
