// Survival LLM's Mac launcher: runs the bundled Python inside this process, so macOS treats it as the app
// (its own Dock icon, name and menu) instead of as "Python". build.sh compiles it into Contents/MacOS.
// Compiled with -DSURVIVAL_LLM_DEV, it is the dev copy's (bin/dev): its own log, and settings from dev.env.
#include <Python.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#ifdef SURVIVAL_LLM_DEV
#define LOG_NAME "Survival LLM Dev.log"
#else
#define LOG_NAME "Survival LLM.log"
#endif

int main(int argc, char **argv) {
    char exe[PATH_MAX], real[PATH_MAX], res[PATH_MAX], home[PATH_MAX], script[PATH_MAX], log[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, real)) return 1;
    *strrchr(real, '/') = '\0';                                  // .../Survival LLM.app/Contents/MacOS
    snprintf(res, sizeof res, "%s/../Resources", real);
    snprintf(home, sizeof home, "%s/python", res);
    snprintf(script, sizeof script, "%s/app/app/server.py", res);
    const char *user = getenv("HOME");
    if (user) {  // keep a log; there is no terminal to print to
        snprintf(log, sizeof log, "%s/Library/Logs", user);
        mkdir(log, 0755);
        snprintf(log, sizeof log, "%s/Library/Logs/" LOG_NAME, user);
        freopen(log, "a", stdout);
        freopen(log, "a", stderr);
        setvbuf(stdout, NULL, _IOLBF, 0);
    }
#ifdef SURVIVAL_LLM_DEV
    // KEY=value lines that keep the dev copy apart from the installed app (its own settings folder). Ones already
    // in the environment win, so bin/dev --fresh can point it somewhere else.
    char env[PATH_MAX], line[4096];
    snprintf(env, sizeof env, "%s/dev.env", res);
    FILE *f = fopen(env, "r");
    if (f) {
        while (fgets(line, sizeof line, f)) {
            line[strcspn(line, "\r\n")] = '\0';
            char *eq = strchr(line, '=');
            if (eq && line[0] != '#') {
                *eq = '\0';
                setenv(line, eq + 1, 0);
            }
        }
        fclose(f);
    }
#endif
    setenv("PYTHONHOME", home, 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);  // the app folder may be read-only
    char *args[] = {argv[0], "-s", script, NULL};
    return Py_BytesMain(3, args);
}
