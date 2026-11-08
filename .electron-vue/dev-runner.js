'use strict'

process.env.NODE_ENV = 'development'

const chalk = require('chalk')
const electron = require('electron')
const path = require('path')
const { say } = require('cfonts')
const { spawn } = require('child_process')
const config = require('../config')
const webpack = require('webpack')
const WebpackDevServer = require('webpack-dev-server')
const Portfinder = require("portfinder")

const mainConfig = require('./webpack.main.config')
const rendererConfig = require('./webpack.renderer.config')

let electronProcess = null
let manualRestart = false

function logStats(proc, data) {
  let log = ''

  log += chalk.yellow.bold(`┏ ${proc} ${config.dev.chineseLog ? '编译过程' : 'Process'} ${new Array((19 - proc.length) + 1).join('-')}`)
  log += '\n\n'

  if (typeof data === 'object') {
    data.toString({
      colors: true,
      chunks: false
    }).split(/\r?\n/).forEach(line => {
      log += '  ' + line + '\n'
    })
  } else {
    log += `  ${data}\n`
  }

  log += '\n' + chalk.yellow.bold(`┗ ${new Array(28 + 1).join('-')}`) + '\n'
  console.log(log)
}

function removeJunk(chunk) {
  if (config.dev.removeElectronJunk) {
    // Example: 2018-08-10 22:48:42.866 Electron[90311:4883863] *** WARNING: Textured window <AtomNSWindow: 0x7fb75f68a770>
    if (/\d+-\d+-\d+ \d+:\d+:\d+\.\d+ Electron(?: Helper)?\[\d+:\d+] /.test(chunk)) {
      return false;
    }

    // Example: [90789:0810/225804.894349:ERROR:CONSOLE(105)] "Uncaught (in promise) Error: Could not instantiate: ProductRegistryImpl.Registry", source: chrome-devtools://devtools/bundled/inspector.js (105)
    if (/\[\d+:\d+\/|\d+\.\d+:ERROR:CONSOLE\(\d+\)\]/.test(chunk)) {
      return false;
    }

    // Example: ALSA lib confmisc.c:767:(parse_card) cannot find card '0'
    if (/ALSA lib [a-z]+\.c:\d+:\([a-z_]+\)/.test(chunk)) {
      return false;
    }
  }


  return chunk;
}

function startRenderer() {
  return new Promise((resolve, reject) => {
    rendererConfig.mode = 'development'
    Portfinder.basePort = config.dev.port || 9088
    Portfinder.getPort((err, port) => {
      if (err) {
        reject("PortError:" + err)
      } else {
        const compiler = webpack(rendererConfig)

        compiler.hooks.done.tap('done', stats => {
          logStats('Renderer', stats)
        })

        const server = new WebpackDevServer(
          {
            client: {
              overlay: false, // 关闭全局错误提示
            },
            port,
            static: {
              directory: path.join(__dirname, '..', 'static'),
              publicPath: '/static/',
            }
          },
          compiler
        )

        process.env.PORT = port
        server.start().then(() => {
          resolve()
        })

      }
    })

  })
}

function startMain() {
  return new Promise((resolve) => {
    mainConfig.mode = 'development'
    const compiler = webpack(mainConfig)

    compiler.hooks.watchRun.tapAsync('watch-run', (compilation, done) => {
      logStats(`${config.dev.chineseLog ? '主进程' : 'Main'}`, chalk.white.bold(`${config.dev.chineseLog ? '正在处理资源文件...' : 'compiling...'}`))
      done()
    })

    compiler.watch({}, (err, stats) => {
      if (err) {
        console.log(err)
        return
      }

      logStats(`${config.dev.chineseLog ? '主进程' : 'Main'}`, stats)

      if (electronProcess && electronProcess.kill) {
        manualRestart = true
        process.kill(electronProcess.pid)
        electronProcess = null
        startElectron()

        setTimeout(() => {
          manualRestart = false
        }, 5000)
      }

      resolve()
    })
  })
}

/** 端口是否有进程在 LISTEN（win32 无 lsof，直接视为空闲） */
function isPortListenFree(port) {
  if (process.platform === 'win32') return true
  try {
    require('child_process').execSync(`lsof -i :${port} -sTCP:LISTEN`, {
      stdio: 'ignore'
    })
    return false
  } catch (_) {
    return true
  }
}

function startElectron() {
  // 应用路径必须在 --inspect 之前：Electron 24 起 --inspect 前置会被
  // default_app 当作应用路径解析，报「Unable to find Electron app at .../--inspect=5858」。
  // 后置写法已验证 inspector 正常（Debugger listening on :5858）。
  var args = [path.join(__dirname, '../dist/electron/main.js')]

  // watch 热重启时旧实例可能尚未释放 5858：被占用则本次不带 --inspect，
  // 避免 inspector 冲突告警；新实例释放端口后重启即恢复调试
  if (isPortListenFree(5858)) {
    args.push('--inspect=5858')
  } else {
    console.log(
      chalk.yellow('[dev] 端口 5858 被占用（旧实例尚未退出），本次启动不带 --inspect')
    )
  }

  // detect yarn or npm and process commandline args accordingly
  if (process.env.npm_execpath.endsWith('yarn.js')) {
    args = args.concat(process.argv.slice(3))
  } else if (process.env.npm_execpath.endsWith('npm-cli.js')) {
    args = args.concat(process.argv.slice(2))
  }

  const childEnv = { ...process.env }
  delete childEnv.ELECTRON_RUN_AS_NODE
  // stdio[0] 用 inherit：本机非交互式终端里 stdin 被管道接管时 Electron 的
  // crashpad 处理器会启动失败并以 crashpad_client_win.cc "not connected" 退出。
  electronProcess = spawn(electron, args, { env: childEnv, stdio: ['inherit', 'pipe', 'pipe'] })

  electronProcess.stdout.on('data', data => {
    electronLog(removeJunk(data), 'blue')
  })
  electronProcess.stderr.on('data', data => {
    electronLog(removeJunk(data), 'red')
  })

  electronProcess.on('close', () => {
    if (!manualRestart) process.exit()
  })
}

function electronLog(data, color) {
  if (data) {
    let log = ''
    data = data.toString().split(/\r?\n/)
    if(data.length){
      if(data[0].indexOf('Failed to fetch extension') != -1 ) return
    }
    data.forEach(line => {
      log += `  ${line}\n`
    })
    console.log(
      chalk[color].bold(`┏ ${config.dev.chineseLog ? '主程序日志' : 'Electron'} -------------------`) +
      '\n\n' +
      log +
      chalk[color].bold('┗ ----------------------------') +
      '\n'
    )
  }

}

function greeting() {
  const cols = process.stdout.columns
  let text = ''

  if (cols > 104) text = 'electron-vue'
  else if (cols > 76) text = 'electron-|vue'
  else text = false

  if (text) {
    say(text, {
      colors: ['yellow'],
      font: 'simple3d',
      space: false
    })
  } else console.log(chalk.yellow.bold('\n  electron-vue'))
  console.log(chalk.blue(`${config.dev.chineseLog ? '  准备启动...' : '  getting ready...'}`) + '\n')
}

async function init() {
  greeting()

  try {
    await startRenderer()
    await startMain()
    await startElectron()
  } catch (error) {
    console.error(error)
  }

}

init()