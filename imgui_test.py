from imgui_bundle import imgui, immapp, hello_imgui

def minimal_test():
    runner_params = hello_imgui.RunnerParams()
    runner_params.app_window_params.window_title = "Video Test"

    def gui_frame():
        imgui.begin("Test Window")
        imgui.text("Hello, this is a test!")
        imgui.end()

    runner_params.callbacks.show_gui = gui_frame
    hello_imgui.run(runner_params)

if __name__ == "__main__":
    minimal_test()