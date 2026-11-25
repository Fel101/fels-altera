"""Altera entry point. Normal launches use Waitress; WSGI hosts use core.app:create_app."""
if __name__ == '__main__':
    from core.launch_cli import parse_args

    args = parse_args()
    from core.runtime import main

    main(args=args)
else:
    from core.app import create_app

    app = create_app(load_addons=False)
