"""CLI commands for the cycle plugin.

Imports command definitions from the plugin package and registers
them on the main Click group via ``cli_group.add_command()``.
"""


def register_commands(cli_group) -> None:
    """Register ``spec-editor cycle`` and ``spec-editor agent`` commands.

    Imports the standalone Click commands/groups from the plugin package
    and adds them as subcommands of the main CLI group.
    """
    from spec_editor_cycle.commands_agent import agent_group
    from spec_editor_cycle.commands_cycle import cycle_cmd, log_clear_cmd, logs_cmd

    cli_group.add_command(cycle_cmd)
    cli_group.add_command(log_clear_cmd)
    cli_group.add_command(logs_cmd)
    cli_group.add_command(agent_group)
